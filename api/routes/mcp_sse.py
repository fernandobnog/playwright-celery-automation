"""
Model Context Protocol (MCP) Streamable HTTP and Server-Sent Events (SSE) Routes.
Implements:
1. MCP Streamable HTTP transport specification (modern standard):
   - Single unified endpoint: POST /mcp (and /mcp/, /mcp/sse) for JSON-RPC messages
   - Supports immediate application/json responses as well as request-scoped text/event-stream
   - Mcp-Session-Id header management and DELETE /mcp session teardown
2. MCP Server-Sent Events (SSE) transport specification (2024-11-05 legacy):
   - GET /mcp/sse for persistent SSE stream with initial 'endpoint' event
   - POST /mcp/messages for asynchronous background execution via Redis Pub/Sub

Features:
- Multi-worker session synchronization via Redis Pub/Sub (handles Uvicorn --workers >= 2)
- Zero-downtime compatibility with all MCP clients (Cursor, Claude Desktop, Antigravity, Cline, Windsurf)
- Resilient session grace period: keeps sessions valid during temporary TCP drops/reconnects
- Thread-isolated JSON-RPC processing (asyncio.to_thread) to prevent event loop blocks
- Keepalive ping generator preventing Cloudflare / Nginx 524 timeouts
"""

import asyncio
import json
import logging
import uuid
from typing import Dict, Optional
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

import redis.asyncio as aioredis
from core.config import settings
from mcp.server import PROTOCOL_VERSION, TOOLS, process_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["Model Context Protocol (Streamable HTTP & Remote SSE)"])

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


# ============================================================================
# 1. Streamable HTTP & SSE GET Endpoints
# ============================================================================

@router.get("/sse", summary="MCP SSE stream endpoint")
@router.get("", summary="MCP unified endpoint (SSE stream or server status)")
@router.get("/", summary="MCP unified endpoint (trailing slash)")
async def mcp_get_endpoint(
    request: Request,
    session_id: Optional[str] = Query(None, description="Optional active session ID"),
):
    """
    Unified GET handler:
    - If client requests 'text/event-stream' (or hits /sse): establishes persistent SSE stream.
    - Otherwise (e.g. browser or health-check): returns server capabilities and status JSON.
    """
    accept_header = request.headers.get("accept", "").lower()
    is_sse_request = (
        "text/event-stream" in accept_header
        or request.url.path.rstrip("/").endswith("/sse")
    )

    if not is_sse_request:
        return JSONResponse(
            status_code=200,
            content={
                "name": "omniflow-lead-enrichment",
                "status": "ONLINE",
                "transport": "streamable-http",
                "supported_transports": ["streamable-http", "sse", "stdio"],
                "protocolVersion": PROTOCOL_VERSION,
                "endpoints": {
                    "mcp": "/mcp",
                    "sse": "/mcp/sse",
                    "messages": "/mcp/messages",
                },
                "tools": [t["name"] for t in TOOLS],
            },
        )

    # Establish SSE Stream
    sid = (
        request.headers.get("mcp-session-id")
        or session_id
        or request.query_params.get("sessionId")
        or request.query_params.get("mcp_session_id")
        or str(uuid.uuid4())
    )
    local_queue: asyncio.Queue = asyncio.Queue()
    active_local_sessions[sid] = local_queue

    # Determine public base URL (respecting proxy headers)
    forwarded_proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "api.fernandonogueira.dev.br"
    base_url = f"{forwarded_proto}://{forwarded_host}".rstrip("/")
    post_endpoint = f"{base_url}/mcp/messages?session_id={sid}"

    logger.info("New MCP SSE client connected. Session ID: %s | Post URL: %s", sid, post_endpoint)

    # Register session in Redis for multi-worker lookup
    redis = await get_redis_client()
    pubsub = None
    if redis:
        try:
            await redis.setex(f"mcp:session:{sid}", SESSION_TTL_SECONDS, "active")
            pubsub = redis.pubsub()
            await pubsub.subscribe(f"mcp:channel:{sid}")
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
                            logger.error("Failed to parse pubsub message for session %s: %s", sid, e)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("Redis listener terminated for session %s: %s", sid, e)

    listener_task = asyncio.create_task(redis_listener()) if pubsub else None

    async def event_generator():
        try:
            # 1. First event required by MCP SSE spec: inform client of the POST endpoint
            yield f"event: endpoint\ndata: {post_endpoint}\n\n"

            while True:
                if await request.is_disconnected():
                    logger.info("MCP SSE client disconnected: %s", sid)
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
            logger.info("MCP SSE stream cancelled for session: %s", sid)
        finally:
            if listener_task:
                listener_task.cancel()
            if pubsub:
                try:
                    await pubsub.unsubscribe(f"mcp:channel:{sid}")
                except Exception:
                    pass
            if redis:
                try:
                    # Retain session key in Redis with remaining TTL so reconnects don't 404
                    await redis.expire(f"mcp:session:{sid}", 300)
                    await redis.aclose()
                except Exception:
                    pass

            active_local_sessions.pop(sid, None)
            logger.info("MCP SSE local session closed for: %s", sid)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Mcp-Session-Id": sid,
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream",
        },
    )


# ============================================================================
# 2. Streamable HTTP POST Endpoints (Direct JSON-RPC Execution)
# ============================================================================

@router.post("", summary="Streamable HTTP MCP POST endpoint")
@router.post("/", summary="Streamable HTTP MCP POST endpoint (trailing slash)")
@router.post("/sse", summary="Streamable HTTP MCP POST endpoint (alias for clients using /mcp/sse)")
async def mcp_post_endpoint(
    request: Request,
    session_id: Optional[str] = Query(None, description="Optional active session ID"),
):
    """
    Handles Streamable HTTP transport POST requests.
    Directly processes JSON-RPC messages (initialize, tools/list, tools/call, ping, etc.)
    and responds with either JSON or a request-scoped SSE stream according to the Accept header.
    """
    sid = (
        request.headers.get("mcp-session-id")
        or session_id
        or request.query_params.get("sessionId")
        or request.query_params.get("mcp_session_id")
        or str(uuid.uuid4())
    )

    # Touch session in Redis to ensure multi-worker tracking and freshness
    redis = await get_redis_client()
    if redis:
        try:
            await redis.setex(f"mcp:session:{sid}", SESSION_TTL_SECONDS, "active")
            await redis.aclose()
        except Exception as e:
            logger.warning("Redis session touch error: %s", e)

    try:
        body_bytes = await request.body()
        body_str = body_bytes.decode("utf-8")
        if not body_str.strip():
            raise ValueError("Corpo da requisição vazio.")
    except Exception as e:
        logger.error("Failed to read MCP POST message body: %s", e)
        raise HTTPException(status_code=400, detail="Corpo da mensagem inválido ou vazio.")

    # Process message synchronously in a thread pool to avoid blocking the async event loop
    response = await asyncio.to_thread(process_message, body_str)

    headers = {"Mcp-Session-Id": sid}

    # If response is None, it was a notification (e.g. notifications/initialized)
    if response is None:
        return Response(status_code=202, content="Accepted", headers=headers)

    accept_header = request.headers.get("accept", "").lower()

    # If client exclusively requested text/event-stream (and does not accept application/json)
    if "text/event-stream" in accept_header and "application/json" not in accept_header:
        async def sse_single_response():
            msg_json = json.dumps(response, ensure_ascii=False)
            yield f"event: message\ndata: {msg_json}\n\n"

        return StreamingResponse(
            sse_single_response(),
            media_type="text/event-stream",
            headers={
                **headers,
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "Content-Type": "text/event-stream",
            },
        )

    # Standard JSON response
    return JSONResponse(
        content=response,
        status_code=200,
        headers=headers,
    )


# ============================================================================
# 3. Streamable HTTP DELETE Endpoints (Session Teardown)
# ============================================================================

@router.delete("", summary="Terminate MCP session")
@router.delete("/", summary="Terminate MCP session (trailing slash)")
@router.delete("/sse", summary="Terminate MCP session (alias)")
async def mcp_delete_endpoint(
    request: Request,
    session_id: Optional[str] = Query(None, description="Optional active session ID"),
):
    """Gracefully terminates an MCP session per the Streamable HTTP transport spec."""
    sid = (
        request.headers.get("mcp-session-id")
        or session_id
        or request.query_params.get("sessionId")
        or request.query_params.get("mcp_session_id")
    )
    if sid:
        active_local_sessions.pop(sid, None)
        redis = await get_redis_client()
        if redis:
            try:
                await redis.delete(f"mcp:session:{sid}")
                await redis.delete(f"mcp:channel:{sid}")
                await redis.aclose()
            except Exception:
                pass
        logger.info("Terminated MCP session: %s", sid)
    return Response(status_code=204)


# ============================================================================
# 4. Legacy MCP SSE Messages Endpoint (2024-11-05 Specification)
# ============================================================================

@router.post("/messages")
async def mcp_messages_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    session_id: str = Query(..., description="ID da sessão SSE ativa"),
):
    """
    Receives JSON-RPC messages from the remote MCP client, returns 202 Accepted immediately,
    and executes the tool in a worker thread, publishing the response back to the SSE stream.
    Retained for backward compatibility with 2024-11-05 SSE clients.
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
