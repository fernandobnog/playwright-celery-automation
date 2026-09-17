"""
Automated unit tests for the Google Sheets to Google Calendar sync flow.
Validates date parsing, summary formatting, future row filtering, and calendar event creation.
"""

from datetime import date
from unittest.mock import MagicMock, patch
import pytest

from flows.flow_calendar_sync import (
    parse_brazilian_date,
    format_event_summary,
    filter_future_sheet_events,
    task_sync_sheets_to_calendar,
)


def test_parse_brazilian_date():
    assert parse_brazilian_date("25/12/2026") == date(2026, 12, 25)
    assert parse_brazilian_date("01/01/2027 19:30") == date(2027, 1, 1)
    assert parse_brazilian_date("") is None
    assert parse_brazilian_date("data_invalida") is None


def test_format_event_summary():
    # Valid times
    s1 = format_event_summary("Bar do Urso", "500", inicio_valido=True, termino_valido=True)
    assert s1 == "Tocar-Bar do Urso-500"

    # Invalid / missing time
    s2 = format_event_summary("Bar do Urso", "500", inicio_valido=False, termino_valido=True)
    assert s2 == "Tocar-Negociar Hora-Bar do Urso-500"


def test_filter_future_sheet_events():
    ref_date = date(2026, 9, 17)
    rows = [
        {"Data": "10/09/2026", "Local": "Passado"},
        {"Data": "17/09/2026", "Local": "Hoje"},
        {"Data": "20/09/2026", "Local": "Futuro 1"},
        {"Data": "01/10/2026", "Local": "Futuro 2"},
    ]
    filtered = filter_future_sheet_events(rows, reference_date=ref_date)
    assert len(filtered) == 2
    assert filtered[0]["Local"] == "Futuro 1"
    assert filtered[1]["Local"] == "Futuro 2"


def test_task_sync_sheets_to_calendar():
    mock_records = [
        {
            "Data": "25/12/2026",
            "Início": "20:00",
            "Término": "23:00",
            "Local": "Cervejaria Campinas",
            "Cache": "600",
            "Observações": "Levar pedestal e extensão",
        }
    ]

    with patch("integrations.google_service.GoogleServicesClient.read_spreadsheet_records", return_value=mock_records), \
         patch("integrations.google_service.GoogleServicesClient.list_calendar_events", return_value=[]), \
         patch("integrations.google_service.GoogleServicesClient.create_calendar_event") as mock_create, \
         patch("storage.repository.repo.log_flow_start"), \
         patch("storage.repository.repo.log_flow_complete"):

        mock_create.return_value = {"id": "cal-evt-123"}

        res = task_sync_sheets_to_calendar()
        assert res["status"] == "SUCCESS"
        assert res["created_count"] == 1
        assert res["skipped_count"] == 0

        # Verify reminders configured for 24h (1440m) and 36h (2160m)
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        assert kwargs["summary"] == "Tocar-Cervejaria Campinas-600"
        assert kwargs["popup_reminder_minutes"] == [1440, 2160]
