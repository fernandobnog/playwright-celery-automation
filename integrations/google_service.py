"""
Google APIs Client (Calendar, Sheets, and Gmail) using OAuth2 / Service Account.
Backed by the modular integrations.google hub while preserving original discovery methods
for direct compatibility with existing tests.
"""

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging
from typing import Any, Dict, List, Optional
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import gspread

from core.config import settings
from integrations.google import GoogleHub

logger = logging.getLogger(__name__)


class GoogleServicesClient:
    """
    Client managing authenticated access to Google Cloud APIs.
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ):
        self.hub = GoogleHub(client_id=client_id, client_secret=client_secret)
        self.client_id = self.hub.auth.client_id
        self.client_secret = self.hub.auth.client_secret

    def get_calendar_credentials(self) -> Credentials:
        return self.hub.auth.get_oauth_credentials("calendar")

    def get_sheets_credentials(self) -> Credentials:
        return self.hub.auth.get_oauth_credentials("sheets")

    def get_gmail_credentials(self) -> Credentials:
        return self.hub.auth.get_oauth_credentials("gmail")

    # --------------------------------------------------------------------------
    # Google Calendar Operations
    # --------------------------------------------------------------------------
    def list_calendar_events(
        self,
        calendar_id: str,
        time_min: str,
        time_max: str,
    ) -> List[Dict[str, Any]]:
        creds = self.get_calendar_credentials()
        service = build("calendar", "v3", credentials=creds)
        events_result = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
            )
            .execute()
        )
        return events_result.get("items", [])

    def create_calendar_event(
        self,
        calendar_id: str,
        summary: str,
        description: str,
        start_iso: str,
        end_iso: str,
        popup_reminder_minutes: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        creds = self.get_calendar_credentials()
        service = build("calendar", "v3", credentials=creds)

        reminders = {"useDefault": True}
        if popup_reminder_minutes:
            reminders = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": m} for m in popup_reminder_minutes],
            }

        event_body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_iso, "timeZone": "America/Sao_Paulo"},
            "end": {"dateTime": end_iso, "timeZone": "America/Sao_Paulo"},
            "reminders": reminders,
        }

        return service.events().insert(calendarId=calendar_id, body=event_body).execute()

    # --------------------------------------------------------------------------
    # Google Sheets Operations
    # --------------------------------------------------------------------------
    def read_spreadsheet_records(
        self,
        spreadsheet_id: str,
        worksheet_title_or_index: Any = 0,
    ) -> List[Dict[str, Any]]:
        creds = self.get_sheets_credentials()
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(spreadsheet_id)

        if isinstance(worksheet_title_or_index, int):
            ws = sh.get_worksheet(worksheet_title_or_index)
        else:
            ws = sh.worksheet(worksheet_title_or_index)

        return ws.get_all_records()

    # --------------------------------------------------------------------------
    # Gmail Operations
    # --------------------------------------------------------------------------
    def send_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
    ) -> Dict[str, Any]:
        creds = self.get_gmail_credentials()
        service = build("gmail", "v1", credentials=creds)

        message = MIMEMultipart("alternative")
        message["to"] = to_email
        message["subject"] = subject
        message.attach(MIMEText(html_body, "html"))

        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        return (
            service.users()
            .messages()
            .send(userId="me", body={"raw": raw_message})
            .execute()
        )
