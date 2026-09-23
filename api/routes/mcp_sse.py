"""
Model Context Protocol (MCP) Server-Sent Events (SSE) Route.
Implements the remote MCP SSE transport specification (2024-11-05).
Allows remote AI clients (Cursor, Claude Desktop, Antigravity) to connect
directly over HTTPS without running local scripts or SSH tunnels.
"""

import asyncio
import json
import logging
import uuid
from typing import Dict
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from mcp.server import process_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["Model Context Protocol (Remote SSE)"])

# Active SSE client sessions: session_id -> asyncio.Queue
active_sessions: Dict[str, asyncio.Queue] = {}


@router.get("/sse")
async def mcp_sse_endpoint(request: Request):
    """
    Establishes a persistent SSE stream with the remote MCP client.
    Sends the initial 'endpoint' event containing the URL where the client
    must POST subsequent JSON-RPC messages.
    """
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    active_sessions[session_id] = queue

    # Determine public base URL (respecting proxy headers)
    forwarded_proto = request.headers.get("x-forwarded-proto", "https")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "api.fernandonogueira.dev.br"
    base_url = f"{forwarded_proto}://{forwarded_host}".rstrip("/")
    post_endpoint = f"{base_url}/mcp/messages?session_id={session_id}"

    logger.info("New MCP SSE client connected. Session ID: %s | Post URL: %s", session_id, post_endpoint)

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
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    msg_json = json.dumps(msg, ensure_ascii=False)
                    yield f"event: message\ndata: {msg_json}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive ping to prevent proxy/Cloudflare timeout
                    yield ": ping\n\n"

        except asyncio.CancelledError:
            logger.info("MCP SSE stream cancelled for session: %s", session_id)
        finally:
            active_sessions.pop(session_id, None)
            logger.info("MCP SSE session closed and cleaned up: %s", session_id)

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
    session_id: str = Query(..., description="ID da sessão SSE ativa"),
):
    """
    Receives JSON-RPC messages from the remote MCP client, processes them,
    and enqueues the response back to the active SSE stream.
    """
    if session_id not in active_sessions:
        logger.warning("Rejected message for non-existent session: %s", session_id)
        raise HTTPException(status_code=404, detail="Sessão MCP não encontrada ou expirada.")

    try:
        body_bytes = await request.body()
        body_str = body_bytes.decode("utf-8")
        if not body_str.strip():
            raise ValueError("Corpo da requisição vazio.")
    except Exception as e:
        logger.error("Failed to read MCP message body: %s", e)
        raise HTTPException(status_code=400, detail="Corpo da mensagem inválido.")

    # Process JSON-RPC message using the standard MCP engine
    try:
        response = process_message(body_str)
        if response is not None:
            queue = active_sessions[session_id]
            await queue.put(response)

        return Response(status_code=202, content="Accepted")
    except Exception as e:
        logger.error("Error processing MCP message for session %s: %s", session_id, e)
        raise HTTPException(status_code=500, detail=f"Erro interno no processamento MCP: {str(e)}")
