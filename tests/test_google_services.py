"""
Unit tests for the modular Google Workspace & Cloud Integration Hub.
Tests Gmail, Sheets, Docs, Drive, Calendar, and Auth Manager with mocked Google APIs.
"""

from unittest.mock import MagicMock, patch
import pytest

from integrations.google import (
    GoogleAuthManager,
    GoogleHub,
    GmailService,
    SheetsService,
    DocsService,
    DriveService,
    CalendarService,
)


@pytest.fixture
def mock_auth():
    auth = MagicMock(spec=GoogleAuthManager)
    creds = MagicMock()
    creds.token = "mock_token"
    creds.valid = True
    auth.get_credentials.return_value = creds
    auth.get_oauth_credentials.return_value = creds
    auth.get_service_account_credentials.return_value = None
    return auth


def test_google_hub_init(mock_auth):
    with patch("integrations.google.GoogleAuthManager", return_value=mock_auth):
        hub = GoogleHub()
        assert hub.calendar is not None
        assert hub.docs is not None
        assert hub.drive is not None
        assert hub.gmail is not None
        assert hub.sheets is not None


def test_auth_manager_oauth_resolution():
    auth = GoogleAuthManager(client_id="test_id", client_secret="test_secret")
    creds = auth.get_oauth_credentials("calendar", custom_refresh_token="mock_refresh")
    assert creds.client_id == "test_id"
    assert creds.client_secret == "test_secret"
    assert creds.refresh_token == "mock_refresh"


# ------------------------------------------------------------------------------
# Gmail Tests
# ------------------------------------------------------------------------------
def test_gmail_send_email(mock_auth):
    service = GmailService(auth_manager=mock_auth)
    mock_gmail_api = MagicMock()
    mock_messages = MagicMock()
    mock_gmail_api.users.return_value.messages.return_value = mock_messages
    mock_messages.send.return_value.execute.return_value = {"id": "msg_123", "threadId": "th_123"}

    with patch("integrations.google.gmail.build", return_value=mock_gmail_api):
        res = service.send_email(
            to_email="lead@example.com",
            subject="Boas-vindas",
            html_body="<p>Olá!</p>",
            cc=["socio@example.com"],
        )
        assert res["id"] == "msg_123"
        mock_messages.send.assert_called_once()


def test_gmail_send_email_with_attachments(mock_auth):
    service = GmailService(auth_manager=mock_auth)
    mock_gmail_api = MagicMock()
    mock_messages = MagicMock()
    mock_gmail_api.users.return_value.messages.return_value = mock_messages
    mock_messages.send.return_value.execute.return_value = {"id": "msg_att_123"}

    with patch("integrations.google.gmail.build", return_value=mock_gmail_api):
        res = service.send_email(
            to_email="cliente@example.com",
            subject="Proposta em Anexo",
            html_body="<p>Segue proposta</p>",
            attachments=[{"filename": "proposta.pdf", "content": b"dummy_pdf", "mimetype": "application/pdf"}],
        )
        assert res["id"] == "msg_att_123"
        mock_messages.send.assert_called_once()


def test_gmail_send_jinja_template(mock_auth):
    service = GmailService(auth_manager=mock_auth)
    mock_gmail_api = MagicMock()
    mock_messages = MagicMock()
    mock_gmail_api.users.return_value.messages.return_value = mock_messages
    mock_messages.send.return_value.execute.return_value = {"id": "msg_tmpl_123"}

    with patch("integrations.google.gmail.build", return_value=mock_gmail_api):
        template_str = "<h1>Olá {{ nome }}</h1>"
        res = service.send_jinja_template(
            to_email="teste@ex.com",
            subject="Template Test",
            template_source=template_str,
            context={"nome": "Fernando"},
            is_file=False,
        )
        assert res["id"] == "msg_tmpl_123"


# ------------------------------------------------------------------------------
# Google Sheets Tests
# ------------------------------------------------------------------------------
def test_sheets_read_records(mock_auth):
    service = SheetsService(auth_manager=mock_auth)
    mock_gc = MagicMock()
    mock_sh = MagicMock()
    mock_ws = MagicMock()
    mock_gc.open_by_key.return_value = mock_sh
    mock_sh.get_worksheet.return_value = mock_ws
    mock_ws.get_all_records.return_value = [
        {"Nome": "Lead 1", "Email": "lead1@test.com"},
        {"Nome": "Lead 2", "Email": "lead2@test.com"},
    ]

    with patch("integrations.google.sheets.gspread.authorize", return_value=mock_gc):
        records = service.read_records("fake_sheet_id", worksheet=0)
        assert len(records) == 2
        assert records[0]["Nome"] == "Lead 1"


def test_sheets_append_row(mock_auth):
    service = SheetsService(auth_manager=mock_auth)
    mock_gc = MagicMock()
    mock_sh = MagicMock()
    mock_ws = MagicMock()
    mock_gc.open_by_key.return_value = mock_sh
    mock_sh.worksheet.return_value = mock_ws
    mock_ws.append_row.return_value = {"updates": {"updatedRows": 1}}

    with patch("integrations.google.sheets.gspread.authorize", return_value=mock_gc):
        res = service.append_row("fake_sheet_id", ["Valor 1", "Valor 2"], worksheet="Aba1")
        assert res["updates"]["updatedRows"] == 1
        mock_ws.append_row.assert_called_once_with(["Valor 1", "Valor 2"], value_input_option="USER_ENTERED")


# ------------------------------------------------------------------------------
# Google Docs Tests
# ------------------------------------------------------------------------------
def test_docs_create_document(mock_auth):
    service = DocsService(auth_manager=mock_auth)
    mock_docs_api = MagicMock()
    mock_docs_api.documents.return_value.create.return_value.execute.return_value = {
        "documentId": "doc_xyz123",
        "title": "Minuta Contratual",
    }

    with patch("integrations.google.docs.build", return_value=mock_docs_api):
        res = service.create_document("Minuta Contratual")
        assert res["documentId"] == "doc_xyz123"


def test_docs_replace_text(mock_auth):
    service = DocsService(auth_manager=mock_auth)
    mock_docs_api = MagicMock()
    mock_docs_api.documents.return_value.batchUpdate.return_value.execute.return_value = {
        "replies": [{"replaceAllText": {"occurrencesChanged": 2}}]
    }

    with patch("integrations.google.docs.build", return_value=mock_docs_api):
        res = service.replace_text(
            document_id="doc_xyz123",
            replacements={"cliente_nome": "Silva Santos", "valor": "R$ 5.000,00"},
        )
        assert "replies" in res
        mock_docs_api.documents.return_value.batchUpdate.assert_called_once()


# ------------------------------------------------------------------------------
# Google Drive Tests
# ------------------------------------------------------------------------------
def test_drive_copy_file(mock_auth):
    service = DriveService(auth_manager=mock_auth)
    mock_drive_api = MagicMock()
    mock_drive_api.files.return_value.copy.return_value.execute.return_value = {
        "id": "copied_file_123",
        "name": "Instancia de Proposta",
    }

    with patch("integrations.google.drive.build", return_value=mock_drive_api):
        res = service.copy_file(
            file_id="template_123",
            new_title="Instancia de Proposta",
            destination_folder_id="folder_456",
        )
        assert res["id"] == "copied_file_123"


def test_drive_create_folder(mock_auth):
    service = DriveService(auth_manager=mock_auth)
    mock_drive_api = MagicMock()
    mock_drive_api.files.return_value.create.return_value.execute.return_value = {
        "id": "folder_789",
        "name": "Clientes 2026",
    }

    with patch("integrations.google.drive.build", return_value=mock_drive_api):
        res = service.create_folder("Clientes 2026")
        assert res["id"] == "folder_789"


# ------------------------------------------------------------------------------
# Google Calendar Tests
# ------------------------------------------------------------------------------
def test_calendar_create_event(mock_auth):
    service = CalendarService(auth_manager=mock_auth)
    mock_cal_api = MagicMock()
    mock_events = MagicMock()
    mock_cal_api.events.return_value = mock_events
    mock_events.insert.return_value.execute.return_value = {
        "id": "ev_123",
        "summary": "Show no Bar Central",
    }

    with patch("integrations.google.calendar.build", return_value=mock_cal_api):
        res = service.create_event(
            calendar_id="cal_123",
            summary="Show no Bar Central",
            description="Lembrar equipamento",
            start_iso="2026-10-01T20:00:00-03:00",
            end_iso="2026-10-01T23:00:00-03:00",
            popup_reminder_minutes=[1440, 2160],
        )
        assert res["id"] == "ev_123"
        mock_events.insert.assert_called_once()
