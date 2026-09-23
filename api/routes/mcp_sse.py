"""
Model Context Protocol (MCP) Server-Sent Events (SSE) Route.
Implements the remote MCP SSE transport specification (2024-11-05).
Allows remote AI clients (Cursor, Claude Desktop, Antigravity) to connect
directly over HTTPS without running local scripts or SSH tunnels.

Features:
- Multi-worker session synchronization via Redis Pub/Sub (handles Uvicorn --workers >= 2)
- Immediate 202 Accepted response on POST /mcp/messages with asynchronous background execution
- Resilient session grace period: keeps sessions valid during temporary TCP drops/reconnects
- Thread-isolated JSON-RPC processing (asyncio.to_thread) to prevent event loop blocks & Playwright sync errors
- Keepalive ping generator preventing Cloudflare / Nginx 524 timeouts
"""

import asyncio
import json
import logging
import uuid
from typing import Dict, Optional
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

import redis.asyncio as aioredis
from core.config import settings
from mcp.server import process_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["Model Context Protocol (Remote SSE)"])

# Local worker in-memory active sessions: session_id -> asyncio.Queue
active_local_sessions: Dict[str, asyncio.Queue] = {}
active_sessions = active_local_sessions  # Alias for backward compatibility

# Session TTL in Redis (seconds): preserves session across worker processes and transient reconnects
SESSION_TTL_SECONDS = 600


async def get_redis_client() -> Optional[aioredis.Redis]:
    """Returns an async Redis client instance without socket timeout for pubsub."""
    try:
        return aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    except Exception as e:
        logger.warning("Could not initialize async Redis for MCP SSE: %s", e)
        return None


@router.get("/sse")
async def mcp_sse_endpoint(request: Request):
    """
    Establishes a persistent SSE stream with the remote MCP client.
    Sends the initial 'endpoint' event containing the URL where the client
    must POST subsequent JSON-RPC messages.
    """
    session_id = str(uuid.uuid4())
    local_queue: asyncio.Queue = asyncio.Queue()
    active_local_sessions[session_id] = local_queue

    # Determine public base URL (respecting proxy headers)
    forwarded_proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "api.fernandonogueira.dev.br"
    base_url = f"{forwarded_proto}://{forwarded_host}".rstrip("/")
    post_endpoint = f"{base_url}/mcp/messages?session_id={session_id}"

    logger.info("New MCP SSE client connected. Session ID: %s | Post URL: %s", session_id, post_endpoint)

    # Register session in Redis for multi-worker lookup
    redis = await get_redis_client()
    pubsub = None
    if redis:
        try:
            await redis.setex(f"mcp:session:{session_id}", SESSION_TTL_SECONDS, "active")
            pubsub = redis.pubsub()
            await pubsub.subscribe(f"mcp:channel:{session_id}")
        except Exception as e:
            logger.warning("Redis session registration error: %s", e)

    # Background task to listen on Redis Pub/Sub channel and route to local_queue
    async def redis_listener():
        if not pubsub:
            return
        try:
            async for message in pubsub.listen():
                if message and message.get("type") == "message":
                    raw_data = message.get("data")
                    if raw_data:
                        try:
                            parsed = json.loads(raw_data)
                            await local_queue.put(parsed)
                        except Exception as e:
                            logger.error("Failed to parse pubsub message for session %s: %s", session_id, e)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("Redis listener terminated for session %s: %s", session_id, e)

    listener_task = asyncio.create_task(redis_listener()) if pubsub else None

    async def event_generator():
        try:
            # 1. First event required by MCP SSE spec: inform client of the POST endpoint
            yield f"event: endpoint\ndata: {post_endpoint}\n\n"

            while True:
                if await request.is_disconnected():
                    logger.info("MCP SSE client disconnected: %s", session_id)
                    break

                try:
                    # Wait up to 15 seconds for an outgoing message
                    msg = await asyncio.wait_for(local_queue.get(), timeout=15.0)
                    msg_json = json.dumps(msg, ensure_ascii=False)
                    yield f"event: message\ndata: {msg_json}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive ping to prevent proxy/Cloudflare timeout
                    yield ": ping\n\n"

        except asyncio.CancelledError:
            logger.info("MCP SSE stream cancelled for session: %s", session_id)
        finally:
            if listener_task:
                listener_task.cancel()
            if pubsub:
                try:
                    await pubsub.unsubscribe(f"mcp:channel:{session_id}")
                except Exception:
                    pass
            if redis:
                try:
                    # Retain session key in Redis with remaining TTL so reconnects don't 404
                    await redis.expire(f"mcp:session:{session_id}", 300)
                    await redis.aclose()
                except Exception:
                    pass

            active_local_sessions.pop(session_id, None)
            logger.info("MCP SSE local session closed for: %s", session_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream",
        },
    )


@router.post("/messages")
async def mcp_messages_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    session_id: str = Query(..., description="ID da sessão SSE ativa"),
):
    """
    Receives JSON-RPC messages from the remote MCP client, returns 202 Accepted immediately,
    and executes the tool in a worker thread, publishing the response back to the SSE stream.
    """
    # 1. Validate session existence across worker processes
    session_valid = session_id in active_local_sessions
    redis = await get_redis_client()

    if not session_valid and redis:
        try:
            exists = await redis.exists(f"mcp:session:{session_id}")
            if exists:
                session_valid = True
                await redis.expire(f"mcp:session:{session_id}", SESSION_TTL_SECONDS)
        except Exception as e:
            logger.warning("Error checking Redis session validity: %s", e)

    if not session_valid:
        if redis:
            await redis.aclose()
        logger.warning("Rejected message for non-existent session: %s", session_id)
        raise HTTPException(status_code=404, detail="Sessão MCP não encontrada ou expirada.")

    try:
        body_bytes = await request.body()
        body_str = body_bytes.decode("utf-8")
        if not body_str.strip():
            raise ValueError("Corpo da requisição vazio.")
    except Exception as e:
        if redis:
            await redis.aclose()
        logger.error("Failed to read MCP message body: %s", e)
        raise HTTPException(status_code=400, detail="Corpo da mensagem inválido.")

    # 2. Asynchronous execution in worker thread with pubsub notification
    async def process_and_publish():
        redis_pub = await get_redis_client()
        try:
            response = await asyncio.to_thread(process_message, body_str)
            if response is not None:
                delivered = False
                if redis_pub:
                    try:
                        sub_count = await redis_pub.publish(
                            f"mcp:channel:{session_id}",
                            json.dumps(response, ensure_ascii=False),
                        )
                        if sub_count > 0:
                            delivered = True
                    except Exception as e_pub:
                        logger.warning("Failed publishing MCP response to Redis: %s", e_pub)

                # Fallback to direct local delivery if on same worker and not delivered via pubsub
                if not delivered and session_id in active_local_sessions:
                    await active_local_sessions[session_id].put(response)
        except Exception as e_proc:
            logger.error("Error processing MCP message in background: %s", e_proc)
        finally:
            if redis_pub:
                await redis_pub.aclose()

    background_tasks.add_task(process_and_publish)

    if redis:
        await redis.aclose()

    return Response(status_code=202, content="Accepted")
