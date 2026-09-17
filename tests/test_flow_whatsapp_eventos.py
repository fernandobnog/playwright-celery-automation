"""
Automated unit tests for Flow: Whatsapp Eventos.
Tests venue classification, phone normalization, anti-spam jitter/skipping,
personalized message generation, and Google Sheets contact timestamp update.
"""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from flows.flow_whatsapp_eventos import (
    parse_date_brazilian,
    classify_venue_schedules,
    build_engagement_message,
    process_and_dispatch_event_leads,
    task_reengajar_locais_eventos,
)


def test_parse_date_brazilian():
    assert parse_date_brazilian("15/10/2026") == date(2026, 10, 15)
    assert parse_date_brazilian("01/01/2027 12:00:00") == date(2027, 1, 1)
    assert parse_date_brazilian("") is None
    assert parse_date_brazilian(None) is None
    assert parse_date_brazilian("invalid_date") is None


def test_classify_venue_schedules():
    ref_date = date(2026, 9, 17)
    agenda_data = [
        {"Local": "Bar do Urso", "Data": "10/08/2026"},  # past
        {"Local": "Zafferano", "Data": "10/08/2026"},    # past
        {"Local": "Zafferano", "Data": "25/12/2026"},    # future
        {"Local": "Areca", "Data": "01/10/2026"},        # future
    ]

    schedule_map = classify_venue_schedules(agenda_data, reference_date=ref_date)

    assert "bar do urso" in schedule_map
    assert schedule_map["bar do urso"]["temPassado"] is True
    assert schedule_map["bar do urso"]["temFuturo"] is False

    assert "zafferano" in schedule_map
    assert schedule_map["zafferano"]["temPassado"] is True
    assert schedule_map["zafferano"]["temFuturo"] is True

    assert "areca" in schedule_map
    assert schedule_map["areca"]["temPassado"] is False
    assert schedule_map["areca"]["temFuturo"] is True


def test_build_engagement_message():
    msg_past = build_engagement_message("Passado sem futuro")
    assert "voltar a tocar pra vocês" in msg_past
    assert "MPB e Pop Rock" in msg_past

    msg_never = build_engagement_message("Nunca teve evento")
    assert "levar meu trabalho para o espaço de vocês" in msg_never
    assert "data teste" in msg_never

    msg_future = build_engagement_message("Tem no futuro")
    assert "animado para o nosso show" in msg_future


def test_process_and_dispatch_event_leads():
    mock_hub = MagicMock()
    mock_evo = MagicMock()

    # Async mock for Evolution WhatsApp check & send
    mock_evo.check_whatsapp_number = AsyncMock(return_value=True)
    mock_evo.send_text_message = AsyncMock(return_value={"status": "SENT", "id": "msg-123"})

    # Mock Sheets data
    agenda_rows = [
        {"Local": "Bar do Urso", "Data": "10/08/2026"},  # Past event
    ]
    local_rows = [
        {
            "Local": "Bar do Urso",
            "MSG": "sim",
            "WhatsApp Principal": "19998256557",
            "Dia último Contato": "01/01/2026",  # > 20 days ago
        },
        {
            "Local": "Bar Ignorado",
            "MSG": "não",
            "WhatsApp Principal": "19998256557",
            "Dia último Contato": "",
        },
        {
            "Local": "Bar Contatado Recentemente",
            "MSG": "sim",
            "WhatsApp Principal": "19998256557",
            "Dia último Contato": date.today().strftime("%d/%m/%Y"),  # Today (skip)
        },
    ]

    mock_hub.sheets.read_records.side_effect = lambda s_id, worksheet: (
        agenda_rows if worksheet == "Agenda" else local_rows
    )

    result = process_and_dispatch_event_leads(
        spreadsheet_id="test_sheet_id",
        max_messages=5,
        min_delay_seconds=0.01,
        max_delay_seconds=0.02,
        hub=mock_hub,
        evolution=mock_evo,
        dry_run=False,
    )

    assert result["status"] == "SUCCESS"
    assert len(result["dispatched"]) == 1
    assert result["dispatched"][0]["venue"] == "Bar do Urso"
    assert result["dispatched"][0]["status_agenda"] == "Passado sem futuro"
    assert len(result["skipped"]) == 2

    mock_evo.check_whatsapp_number.assert_called_once()
    mock_evo.send_text_message.assert_called_once()
    mock_hub.sheets.find_and_update_row.assert_called_once()


def test_task_reengajar_locais_eventos():
    with patch("flows.flow_whatsapp_eventos.process_and_dispatch_event_leads") as mock_process, \
         patch("storage.repository.repo.log_flow_start"), \
         patch("storage.repository.repo.log_flow_complete"):

        mock_process.return_value = {
            "status": "SUCCESS",
            "dispatched": [{"venue": "Test Venue"}],
            "skipped": [],
        }

        res = task_reengajar_locais_eventos(max_messages=2, dry_run=True)
        assert res["status"] == "SUCCESS"
        mock_process.assert_called_once_with(
            spreadsheet_id="1qNxFZgzQREMsPT0yDtqkvJ-qg8lrhbY0CP15nC4NdSc",
            max_messages=2,
            dry_run=True,
        )
