"""
Unit tests for the OmniFlow Model Context Protocol (MCP) Server.
Validates protocol handshake, tool listing, tool dispatch,
and both Streamable HTTP and SSE transport specifications.
"""

from unittest.mock import patch
import json
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routes.mcp_sse import active_sessions
from core.config import settings
from mcp.server import (
    handle_initialize,
    handle_tools_call,
    handle_tools_list,
    handle_resources_list,
    handle_prompts_list,
    process_message,
)

AUTH_HEADERS = {
    "X-API-Key": settings.INTERNAL_API_KEY or "omniflow_232750db9cac2682c20ffadd0bce268f2d85764bc1149921"
}


def test_mcp_initialize():
    """Validates MCP protocol handshake and protocol version negotiation."""
    req_id = 1
    params = {
        "protocolVersion": "2024-11-05",
        "clientInfo": {"name": "claude-desktop", "version": "0.1.0"},
    }
    resp = handle_initialize(req_id, params)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["serverInfo"]["name"] == "omniflow-lead-enrichment"

    # Test negotiation of newer Streamable HTTP protocol version
    resp_v2 = handle_initialize(2, {"protocolVersion": "2025-03-26"})
    assert resp_v2["result"]["protocolVersion"] == "2025-03-26"


def test_mcp_tools_list():
    """Validates that all enrichment tools are exposed."""
    resp = handle_tools_list(2)
    assert resp["id"] == 2
    tools = resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "enrich_company" in tool_names
    assert "find_decision_makers" in tool_names
    assert "get_linkedin_company_profile" in tool_names
    assert "verify_email" in tool_names


def test_mcp_resources_and_prompts_list():
    """Validates that resources and prompts lists return empty arrays."""
    res = handle_resources_list(3)
    assert res["result"]["resources"] == []
    prm = handle_prompts_list(4)
    assert prm["result"]["prompts"] == []


def test_mcp_tools_call_enrich_company():
    """Validates tool execution with mocked HTTP response."""
    mock_payload = {
        "status": "SUCCESS",
        "nome_pesquisado": "Matera",
        "google": {"cnpj": "58.749.123/0001-00"},
        "linkedin": {"total_decisores_encontrados": 2},
    }

    with patch("mcp.server._http_request", return_value=mock_payload) as mock_req:
        resp = handle_tools_call(
            3,
            {
                "name": "enrich_company",
                "arguments": {"name": "Matera", "location": "Campinas SP"},
            },
        )
        assert resp["id"] == 3
        assert resp["result"]["isError"] is False
        content_text = resp["result"]["content"][0]["text"]
        assert "58.749.123/0001-00" in content_text
        mock_req.assert_called_once()


def test_mcp_process_message_ping_and_unknown():
    """Validates ping and unknown method handling."""
    ping_resp = process_message(json.dumps({"jsonrpc": "2.0", "id": 10, "method": "ping"}))
    assert ping_resp == {"jsonrpc": "2.0", "id": 10, "result": {}}

    unknown_resp = process_message(json.dumps({"jsonrpc": "2.0", "id": 11, "method": "non_existent"}))
    assert unknown_resp["error"]["code"] == -32601

    # Test notification (should return None)
    notif_resp = process_message(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    assert notif_resp is None

    # Test batch message
    batch_resp = process_message([
        {"jsonrpc": "2.0", "id": 20, "method": "ping"},
        {"jsonrpc": "2.0", "id": 21, "method": "ping"},
    ])
    assert isinstance(batch_resp, list)
    assert len(batch_resp) == 2
    assert batch_resp[0]["id"] == 20
    assert batch_resp[1]["id"] == 21


def test_mcp_streamable_http_post_initialize():
    """Validates Streamable HTTP POST /mcp initialize request."""
    client = TestClient(app, headers=AUTH_HEADERS)

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "clientInfo": {"name": "cursor", "version": "0.45.0"},
            "capabilities": {},
        },
    }
    resp = client.post(
        "/mcp",
        json=payload,
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert resp.status_code == 200
    assert "Mcp-Session-Id" in resp.headers
    data = resp.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 1
    assert data["result"]["serverInfo"]["name"] == "omniflow-lead-enrichment"
    assert data["result"]["protocolVersion"] == "2025-03-26"


def test_mcp_streamable_http_post_tools_list():
    """Validates Streamable HTTP POST /mcp tools/list request."""
    client = TestClient(app, headers=AUTH_HEADERS)

    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    assert "Mcp-Session-Id" in resp.headers
    data = resp.json()
    assert data["id"] == 2
    tool_names = [t["name"] for t in data["result"]["tools"]]
    assert "enrich_company" in tool_names


def test_mcp_streamable_http_post_notification():
    """Validates Streamable HTTP POST /mcp notifications/initialized returning 202."""
    client = TestClient(app, headers=AUTH_HEADERS)

    payload = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 202
    assert resp.text == "Accepted"


def test_mcp_streamable_http_post_sse_alias():
    """
    Validates Streamable HTTP POST /mcp/sse.
    Ensures clients configuring /mcp/sse with Streamable HTTP no longer receive 405 Method Not Allowed.
    """
    client = TestClient(app, headers=AUTH_HEADERS)

    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "ping",
    }
    resp = client.post("/mcp/sse", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 3
    assert data["result"] == {}


def test_mcp_streamable_http_exclusive_sse_accept():
    """Validates Streamable HTTP POST returning text/event-stream when Accept is text/event-stream."""
    client = TestClient(app, headers=AUTH_HEADERS)

    payload = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "ping",
    }
    resp = client.post(
        "/mcp",
        json=payload,
        headers={"Accept": "text/event-stream"},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert "event: message" in resp.text
    assert '"id": 4' in resp.text


def test_mcp_streamable_http_delete_session():
    """Validates Streamable HTTP session termination via DELETE /mcp."""
    client = TestClient(app, headers=AUTH_HEADERS)

    resp = client.delete("/mcp", headers={"Mcp-Session-Id": "session-to-delete-123"})
    assert resp.status_code == 204


def test_mcp_get_status():
    """Validates non-SSE GET /mcp returns server status and capabilities."""
    client = TestClient(app, headers=AUTH_HEADERS)

    resp = client.get("/mcp", headers={"Accept": "application/json"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "omniflow-lead-enrichment"
    assert data["status"] == "ONLINE"
    assert data["transport"] == "streamable-http"
    assert "enrich_company" in data["tools"]


def test_mcp_sse_and_messages_endpoints():
    """Tests the legacy remote SSE route presence and session posting flow."""
    client = TestClient(app, headers=AUTH_HEADERS)

    # 1. Test routes are registered in FastAPI Gateway OpenAPI schema
    paths = list(app.openapi()["paths"].keys())
    assert "/mcp" in paths or "/mcp/" in paths
    assert "/mcp/sse" in paths
    assert "/mcp/messages" in paths

    # 2. Test POST /mcp/messages without valid session -> 404
    bad_resp = client.post("/mcp/messages?session_id=invalid-session", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert bad_resp.status_code == 404

    # 3. Test POST /mcp/messages with active session -> 202 Accepted
    test_session_id = "test-session-123"
    import asyncio
    test_queue = asyncio.Queue()
    active_sessions[test_session_id] = test_queue

    try:
        ok_resp = client.post(
            f"/mcp/messages?session_id={test_session_id}",
            json={"jsonrpc": "2.0", "id": 99, "method": "ping"},
        )
        import time
        start_wait = time.time()
        while test_queue.empty() and time.time() - start_wait < 2.0:
            time.sleep(0.05)

        assert not test_queue.empty()
        queued_msg = test_queue.get_nowait()
        assert queued_msg["id"] == 99
    finally:
        active_sessions.pop(test_session_id, None)
