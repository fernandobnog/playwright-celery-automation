"""
Unit tests for FastAPI endpoints using TestClient.
"""

from fastapi.testclient import TestClient
from api.main import app
from core.config import settings

client = TestClient(app, headers={"X-API-Key": settings.INTERNAL_API_KEY or "omniflow_232750db9cac2682c20ffadd0bce268f2d85764bc1149921"})


def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "Omni-Flow" in data["name"]
    assert "vnc_live_streams" in data


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data


def test_vnc_info_endpoint():
    response = client.get("/api/v1/vnc-info")
    assert response.status_code == 200
    data = response.json()
    assert "worker_1_novnc" in data
    assert "worker_2_novnc" in data


def test_incoming_webhook_endpoint():
    payload = {
        "event": "customer.inquiry",
        "source": "zendesk",
        "payload": {"ticket_id": 999}
    }
    response = client.post("/api/v1/webhooks/incoming", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ACCEPTED"
    assert data["event_received"] == "customer.inquiry"


def test_flows_whatsapp_eventos_endpoint():
    from unittest.mock import patch
    with patch("flows.flow_whatsapp_eventos.task_reengajar_locais_eventos.delay") as mock_delay:
        mock_delay.return_value.id = "mock-task-wpp-123"
        response = client.post("/api/v1/flows/whatsapp-eventos", json={"max_messages": 3, "dry_run": True})
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "mock-task-wpp-123"
        assert data["status"] == "QUEUED"


def test_sync_agenda_endpoint():
    from unittest.mock import patch
    with patch("flows.flow_site_sync.task_sync_site_agenda.delay") as mock_delay:
        mock_delay.return_value.id = "mock-task-sync-1"
        response = client.post("/api/v1/sync/agenda")
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "mock-task-sync-1"
        assert data["status"] == "QUEUED"


def test_sync_repertorio_endpoint():
    from unittest.mock import patch
    with patch("flows.flow_site_sync.task_sync_site_repertorio.delay") as mock_delay:
        mock_delay.return_value.id = "mock-task-sync-2"
        response = client.post("/api/v1/sync/repertorio")
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "mock-task-sync-2"
        assert data["status"] == "QUEUED"


def test_proposals_generate_endpoint_async():
    from unittest.mock import patch
    payload = {
        "client_name": "Teste Noiva",
        "client_phone": "19998256557",
        "event_date": "10/12/2026",
        "venue_name": "Chácara Bela Vista",
        "cache_value": "R$ 1.200,00",
        "send_whatsapp": False,
    }
    with patch("flows.flow_proposal_generator.task_generate_and_send_proposal.delay") as mock_delay:
        mock_delay.return_value.id = "mock-task-prop-1"
        response = client.post("/api/v1/proposals/generate?async_mode=true", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "mock-task-prop-1"
        assert data["status"] == "QUEUED"


def test_editorial_token_cycle():
    from core.security import create_editorial_action_token, verify_editorial_action_token
    token = create_editorial_action_token(
        pauta_id=1,
        pauta_titulo="IA no Desenvolvimento",
        categoria="Tecnologia da Informação (TI)",
        target_format="both",
    )
    assert token is not None
    payload = verify_editorial_action_token(token)
    assert payload is not None
    assert payload["pauta_id"] == 1
    assert payload["pauta_titulo"] == "IA no Desenvolvimento"

    # Test invalid token
    assert verify_editorial_action_token("invalid.token") is None
    assert verify_editorial_action_token("") is None


def test_editorial_select_endpoint_invalid_token():
    response = client.get("/api/v1/editorial/select?token=invalid_token_123")
    assert response.status_code == 400
    assert "Link Expirado ou Inválido" in response.text


def test_editorial_select_endpoint_valid_token():
    from unittest.mock import patch
    from core.security import create_editorial_action_token

    token = create_editorial_action_token(
        pauta_id=2,
        pauta_titulo="O Mercado Musical em 2026",
        categoria="Música & Mercado Musical",
    )
    with patch("flows.flow_content_deep_writer.task_deep_content_generation.delay") as mock_delay:
        mock_delay.return_value.id = "mock-deep-task-789"
        response = client.get(f"/api/v1/editorial/select?token={token}")
        assert response.status_code == 200
        assert "Tema Selecionado com Sucesso!" in response.text
        assert "O Mercado Musical em 2026" in response.text
        mock_delay.assert_called_once()
