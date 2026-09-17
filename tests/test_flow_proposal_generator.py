"""
Automated unit tests for Flow: Proposal & Contract Generator.
Tests Pydantic validation, Google Docs generation/templating, Drive PDF export,
WhatsApp dispatch via EvolutionClient, Email dispatch via Gmail, and Celery task execution.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from pydantic import ValidationError

from flows.flow_proposal_generator import (
    ProposalRequest,
    generate_proposal_pdf_and_send,
    task_generate_and_send_proposal,
)


def test_proposal_request_schema_valid():
    req = ProposalRequest(
        client_name="Maria Silva",
        client_phone="(19) 99825-6557",
        event_date="20/12/2026",
        venue_name="Bar do Urso",
        cache_value="R$ 700,00",
    )
    assert req.client_name == "Maria Silva"
    assert req.event_time == "20:00 às 23:00"
    assert req.venue_city == "Mogi Mirim / SP"
    assert req.send_whatsapp is True
    assert req.send_email is None


def test_proposal_request_schema_missing_required():
    with pytest.raises(ValidationError):
        ProposalRequest(
            client_name="Maria Silva",
            # missing client_phone, event_date, venue_name, cache_value
        )


def test_generate_proposal_pdf_and_send_success(tmp_path):
    mock_hub = MagicMock()
    mock_evo = MagicMock()

    mock_hub.docs.create_document.return_value = {"documentId": "doc-test-123"}
    mock_hub.drive.export_as_pdf.return_value = b"%PDF-1.4 sample proposal data"
    mock_evo.send_media_message = AsyncMock(return_value={"status": "SUCCESS", "id": "media-wpp-1"})

    req = ProposalRequest(
        client_name="Carlos Eduardo",
        client_phone="19998256557",
        event_date="15/11/2026",
        event_time="21:00 às 00:00",
        venue_name="Cervejaria Artesanal",
        venue_city="Mogi Guaçu / SP",
        cache_value="R$ 800,00",
        repertoire_style="Pop Rock Nacional e Internacional",
        observations="Levar som completo e microfones adicionais.",
        send_whatsapp=True,
        send_email="carlos@exemplo.com",
    )

    result = generate_proposal_pdf_and_send(
        req=req,
        hub=mock_hub,
        evolution=mock_evo,
        output_dir=str(tmp_path),
    )

    assert result["status"] == "SUCCESS"
    assert result["document_id"] == "doc-test-123"
    assert "Proposta_Show_Carlos_Eduardo_15-11-2026.pdf" in result["pdf_filename"]
    assert result["delivery"]["whatsapp"] == "SENT"
    assert result["delivery"]["email"] == "SENT"

    # Verify docs & drive calls
    mock_hub.docs.create_document.assert_called_once()
    mock_hub.docs.append_text.assert_called_once()
    mock_hub.docs.replace_text.assert_called_once()
    mock_hub.drive.export_as_pdf.assert_called_once()

    # Verify Evolution dispatch
    mock_evo.send_media_message.assert_called_once()
    evo_call_args = mock_evo.send_media_message.call_args.kwargs
    assert evo_call_args["phone"] == "5519998256557"
    assert evo_call_args["mime_type"] == "application/pdf"

    # Verify Gmail dispatch
    mock_hub.gmail.send_email.assert_called_once()
    gmail_call_args = mock_hub.gmail.send_email.call_args.kwargs
    assert gmail_call_args["to_email"] == "carlos@exemplo.com"
    assert len(gmail_call_args["attachments"]) == 1


def test_generate_proposal_with_template(tmp_path):
    mock_hub = MagicMock()
    mock_evo = MagicMock()

    mock_hub.drive.copy_file.return_value = {"id": "copied-doc-999"}
    mock_hub.drive.export_as_pdf.return_value = b"%PDF-1.4 template data"

    req = ProposalRequest(
        client_name="Ana Paula",
        client_phone="19998256557",
        event_date="05/12/2026",
        venue_name="Restaurante Zafferano",
        cache_value="R$ 600,00",
        template_id="google-drive-template-file-id",
        send_whatsapp=False,
    )

    result = generate_proposal_pdf_and_send(
        req=req,
        hub=mock_hub,
        evolution=mock_evo,
        output_dir=str(tmp_path),
    )

    assert result["status"] == "SUCCESS"
    assert result["document_id"] == "copied-doc-999"
    mock_hub.drive.copy_file.assert_called_once_with(
        "google-drive-template-file-id",
        new_title="Proposta Show - Ana Paula - 05-12-2026",
    )
    mock_hub.docs.create_document.assert_not_called()
    assert result["delivery"]["whatsapp"] == "SKIPPED"


def test_task_generate_and_send_proposal():
    request_data = {
        "client_name": "Juliana Costa",
        "client_phone": "19998256557",
        "event_date": "10/10/2026",
        "venue_name": "Areca Lounge",
        "cache_value": "R$ 500,00",
        "send_whatsapp": False,
    }

    with patch("flows.flow_proposal_generator.generate_proposal_pdf_and_send") as mock_gen, \
         patch("storage.repository.repo.log_flow_start"), \
         patch("storage.repository.repo.log_flow_complete"):

        mock_gen.return_value = {"status": "SUCCESS", "pdf_filename": "test.pdf"}
        res = task_generate_and_send_proposal(request_data)

        assert res["status"] == "SUCCESS"
        mock_gen.assert_called_once()
