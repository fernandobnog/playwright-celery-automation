"""
Unified Google Workspace & Cloud Integration Hub.
Exposes services for Gmail, Sheets, Docs, Drive, and Calendar.
"""

from typing import Optional

from integrations.google.auth import GoogleAuthManager, ALL_COMBINED_SCOPES, DEFAULT_SCOPES
from integrations.google.calendar import CalendarService
from integrations.google.docs import DocsService
from integrations.google.drive import DriveService
from integrations.google.gmail import GmailService
from integrations.google.sheets import SheetsService


class GoogleHub:
    """
    Consolidated facade offering all Google Cloud & Workspace services.
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        service_account_path: Optional[str] = None,
    ):
        self.auth = GoogleAuthManager(
            client_id=client_id,
            client_secret=client_secret,
            service_account_path=service_account_path,
        )
        self.calendar = CalendarService(self.auth)
        self.docs = DocsService(self.auth)
        self.drive = DriveService(self.auth)
        self.gmail = GmailService(self.auth)
        self.sheets = SheetsService(self.auth)


__all__ = [
    "GoogleHub",
    "GoogleAuthManager",
    "CalendarService",
    "DocsService",
    "DriveService",
    "GmailService",
    "SheetsService",
    "ALL_COMBINED_SCOPES",
    "DEFAULT_SCOPES",
]
