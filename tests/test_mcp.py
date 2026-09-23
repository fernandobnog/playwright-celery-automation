"""
Unit tests for the OmniFlow Model Context Protocol (MCP) Server.
Validates protocol handshake, tool listing, and tool dispatch.
"""

from unittest.mock import patch
import json
import pytest

from mcp.server import (
    handle_initialize,
    handle_tools_call,
    handle_tools_list,
    process_message,
)


def test_mcp_initialize():
    """Validates MCP protocol handshake."""
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


def test_mcp_tools_list():
    """Validates that all three enrichment tools are exposed."""
    resp = handle_tools_list(2)
    assert resp["id"] == 2
    tools = resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "enrich_company" in tool_names
    assert "find_decision_makers" in tool_names
    assert "get_linkedin_company_profile" in tool_names


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


def test_mcp_sse_and_messages_endpoints():
    """Tests the remote SSE route presence and session posting flow."""
    from fastapi.testclient import TestClient
    from api.main import app
    from api.routes.mcp_sse import active_sessions
    from core.config import settings
    import asyncio

    client = TestClient(app, headers={"X-API-Key": settings.INTERNAL_API_KEY or "omniflow_232750db9cac2682c20ffadd0bce268f2d85764bc1149921"})

    # 1. Test routes are registered in FastAPI Gateway OpenAPI schema
    paths = list(app.openapi()["paths"].keys())
    assert "/mcp/sse" in paths
    assert "/mcp/messages" in paths

    # 2. Test POST /mcp/messages without valid session -> 404
    bad_resp = client.post("/mcp/messages?session_id=invalid-session", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert bad_resp.status_code == 404

    # 3. Test POST /mcp/messages with active session -> 202 Accepted
    test_session_id = "test-session-123"
    test_queue = asyncio.Queue()
    active_sessions[test_session_id] = test_queue

    try:
        ok_resp = client.post(
            f"/mcp/messages?session_id={test_session_id}",
            json={"jsonrpc": "2.0", "id": 99, "method": "ping"},
        )
        assert ok_resp.status_code == 202
        assert not test_queue.empty()
        queued_msg = test_queue.get_nowait()
        assert queued_msg["id"] == 99
    finally:
        active_sessions.pop(test_session_id, None)


