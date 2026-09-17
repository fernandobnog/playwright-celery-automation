"""
Automated unit and integration tests for the Lead Qualification & Twenty CRM Flow.
Tests name parsing, approval token creation/verification, Celery qualification task, and API endpoints.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from api.main import app
from core.security import create_approval_token, verify_approval_token
from flows.flow_lead_qualification import (
    parse_name,
    task_process_lead_qualification,
    generate_approval_email_html,
)
from integrations.gemini import LeadAuditResult


# ==============================================================================
# 1. Unit Tests for Helpers
# ==============================================================================
def test_parse_name():
    assert parse_name("Fernando Nogueira") == ("Fernando", "Nogueira")
    assert parse_name("Fernando") == ("Fernando", "")
    assert parse_name("Ana Paula de Souza") == ("Ana", "Paula de Souza")
    assert parse_name("") == ("", "")


def test_approval_token_cycle():
    token = create_approval_token("5519998256557", "Fernando", expires_in_seconds=3600)
    assert token is not None
    assert "." in token

    # Valid token
    payload = verify_approval_token(token)
    assert payload is not None
    assert payload["phone"] == "5519998256557"
    assert payload["first_name"] == "Fernando"

    # Tampered token
    tampered = token[:-4] + "abcd"
    assert verify_approval_token(tampered) is None

    # Expired token
    expired_token = create_approval_token("5519998256557", "Fernando", expires_in_seconds=-10)
    assert verify_approval_token(expired_token) is None


def test_generate_approval_email_html():
    html = generate_approval_email_html(
        name="Carlos Silva",
        email="carlos@example.com",
        phone="5519999999999",
        subject="Orçamento",
        message="Gostaria de um orçamento",
        approve_url="https://example.com/approve?token=xyz",
    )
    assert "Carlos Silva" in html
    assert "https://example.com/approve?token=xyz" in html
    assert "Aprovar & Enviar Mensagem" in html


# ==============================================================================
# 2. Pipeline Tests (task_process_lead_qualification)
# ==============================================================================
def test_task_process_lead_qualification_rejected():
    mock_audit = LeadAuditResult(
        aprovado=False,
        status="GOLPE",
        score_confianca=0.99,
        acao_sugerida="descartar",
        motivo="Tentativa explícita de phishing e link encurtado.",
    )

    with patch("integrations.gemini.GeminiClient.generate_structured", return_value=mock_audit), \
         patch("storage.repository.repo.log_flow_start"), \
         patch("storage.repository.repo.log_flow_complete"):

        payload = {
            "name": "Hacker",
            "email": "scam@phish.net",
            "phone": "11999999999",
            "message": "Clique no link para resgatar seu prêmio",
        }

        result = task_process_lead_qualification(payload)
        assert result["status"] == "REJECTED"
        assert result["audit"]["status"] == "GOLPE"


def test_task_process_lead_qualification_approved():
    mock_audit = LeadAuditResult(
        aprovado=True,
        status="REAL",
        score_confianca=0.96,
        acao_sugerida="avancar",
        motivo="Lead qualificado para desenvolvimento de software.",
    )

    with patch("integrations.gemini.GeminiClient.generate_structured", return_value=mock_audit), \
         patch("integrations.twenty.TwentyCRMClient.create_note", new_callable=AsyncMock, return_value="note-123"), \
         patch("integrations.twenty.TwentyCRMClient.find_person_by_email", new_callable=AsyncMock, return_value=None), \
         patch("integrations.twenty.TwentyCRMClient.create_person", new_callable=AsyncMock, return_value="person-456"), \
         patch("integrations.twenty.TwentyCRMClient.link_note_to_person", new_callable=AsyncMock, return_value={}), \
         patch("integrations.evolution.EvolutionClient.check_whatsapp_number", new_callable=AsyncMock, return_value=True), \
         patch("integrations.evolution.EvolutionClient.send_text_message", new_callable=AsyncMock, return_value={}), \
         patch("integrations.google_service.GoogleServicesClient.send_email", return_value={}), \
         patch("storage.repository.repo.log_flow_start"), \
         patch("storage.repository.repo.log_flow_complete"):

        payload = {
            "name": "Juliana Santos",
            "email": "juliana@techcorp.com.br",
            "phone": "19998256557",
            "subject": "Desenvolvimento Web",
            "message": "Precisamos de uma consultoria em automação e Python.",
        }

        result = task_process_lead_qualification(payload)
        assert result["status"] == "APPROVED"
        assert result["person_id"] == "person-456"
        assert result["note_id"] == "note-123"
        assert result["has_whatsapp"] is True
        assert result["approval_dispatched"] is True


# ==============================================================================
# 3. API Route Tests
# ==============================================================================
client = TestClient(app)


def test_api_contact_form_endpoint():
    with patch.object(task_process_lead_qualification, "apply_async") as mock_apply:
        mock_task = MagicMock()
        mock_task.id = "task-uuid-999"
        mock_apply.return_value = mock_task

        resp = client.post(
            "/api/v1/leads/contact-form",
            json={
                "name": "Roberto Costa",
                "email": "roberto@costa.com.br",
                "phone": "19987654321",
                "subject": "Parceria",
                "message": "Olá Fernando, gostaria de bater um papo.",
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "ACCEPTED"
        assert data["task_id"] == "task-uuid-999"


def test_api_approve_endpoint_valid_token():
    token = create_approval_token("5519998256557", "Roberto")

    with patch("integrations.evolution.EvolutionClient.send_text_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"status": "SUCCESS"}
        resp = client.get(f"/api/v1/leads/approve?token={token}")

        assert resp.status_code == 200
        assert "Mensagem Enviada!" in resp.text
        assert "Roberto" in resp.text
        mock_send.assert_called_once()


def test_api_approve_endpoint_invalid_token():
    resp = client.get("/api/v1/leads/approve?token=invalid.token")
    assert resp.status_code == 400
    assert "Link expirado ou inválido" in resp.text
