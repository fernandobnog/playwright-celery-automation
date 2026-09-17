"""
Google Authentication Manager.
Provides unified credentials resolution supporting both OAuth 2.0 (user context)
and Service Accounts (headless background daemon), with automatic token refresh
and in-memory credential caching.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials

from core.config import settings

logger = logging.getLogger(__name__)

# Standard Google API Scopes
DEFAULT_SCOPES = {
    "gmail": [
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.compose",
    ],
    "calendar": [
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/calendar.events",
    ],
    "sheets": [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ],
    "docs": [
        "https://www.googleapis.com/auth/documents",
        "https://www.googleapis.com/auth/drive.file",
    ],
    "drive": [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/drive.file",
    ],
}

ALL_COMBINED_SCOPES = list(
    set(
        DEFAULT_SCOPES["gmail"]
        + DEFAULT_SCOPES["calendar"]
        + DEFAULT_SCOPES["sheets"]
        + DEFAULT_SCOPES["docs"]
        + DEFAULT_SCOPES["drive"]
    )
)


class GoogleAuthManager:
    """
    Central authentication broker for all Google APIs.
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        service_account_path: Optional[str] = None,
    ):
        self.client_id = client_id or settings.GOOGLE_CLIENT_ID
        self.client_secret = client_secret or settings.GOOGLE_CLIENT_SECRET
        self.service_account_path = service_account_path or getattr(
            settings, "GOOGLE_SERVICE_ACCOUNT_FILE", "/app/credentials/service_account.json"
        )
        self._cached_credentials: Dict[str, Any] = {}

    def get_service_account_credentials(
        self, scopes: Optional[List[str]] = None
    ) -> Optional[ServiceAccountCredentials]:
        """
        Loads Google Service Account credentials if the key file or env JSON exists.
        """
        cache_key = f"sa_{'_'.join(sorted(scopes or []))}"
        if cache_key in self._cached_credentials:
            creds = self._cached_credentials[cache_key]
            if creds.expired and creds.refresh_handler:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    logger.warning("Could not refresh Service Account token: %s", e)
            return creds

        # 1. Try JSON file path
        sa_file = Path(self.service_account_path)
        if not sa_file.exists():
            # Fallback to relative path if not in container
            sa_file = Path("credentials/service_account.json")

        if sa_file.exists():
            try:
                creds = ServiceAccountCredentials.from_service_account_file(
                    str(sa_file), scopes=scopes or ALL_COMBINED_SCOPES
                )
                self._cached_credentials[cache_key] = creds
                return creds
            except Exception as e:
                logger.error("Failed to load service account file %s: %s", sa_file, e)

        # 2. Try JSON environment string
        sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if sa_json:
            try:
                data = json.loads(sa_json)
                creds = ServiceAccountCredentials.from_service_account_info(
                    data, scopes=scopes or ALL_COMBINED_SCOPES
                )
                self._cached_credentials[cache_key] = creds
                return creds
            except Exception as e:
                logger.error("Failed to load GOOGLE_SERVICE_ACCOUNT_JSON env: %s", e)

        return None

    def get_oauth_credentials(
        self,
        service_name: str = "default",
        scopes: Optional[List[str]] = None,
        custom_refresh_token: Optional[str] = None,
    ) -> Credentials:
        """
        Resolves OAuth 2.0 user credentials with auto-refresh capability.
        Supports unified GOOGLE_REFRESH_TOKEN or service-specific tokens.
        """
        # Determine refresh token by service precedence
        refresh_token = custom_refresh_token
        client_secret = self.client_secret

        if not refresh_token:
            if service_name == "calendar":
                refresh_token = (
                    getattr(settings, "GOOGLE_CALENDAR_REFRESH_TOKEN", None)
                    or getattr(settings, "GOOGLE_REFRESH_TOKEN", None)
                )
            elif service_name == "sheets":
                refresh_token = (
                    getattr(settings, "GOOGLE_SHEETS_REFRESH_TOKEN", None)
                    or getattr(settings, "GOOGLE_REFRESH_TOKEN", None)
                )
            elif service_name == "gmail":
                refresh_token = (
                    getattr(settings, "GMAIL_REFRESH_TOKEN", None)
                    or getattr(settings, "GOOGLE_REFRESH_TOKEN", None)
                )
                client_secret = getattr(settings, "GMAIL_CLIENT_SECRET", None) or self.client_secret
            else:
                refresh_token = (
                    getattr(settings, "GOOGLE_REFRESH_TOKEN", None)
                    or getattr(settings, "GOOGLE_SHEETS_REFRESH_TOKEN", None)
                )

        # By default, keep scopes as None so Google automatically honors the scopes
        # originally granted when the refresh token was generated.
        target_scopes = scopes

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=client_secret,
            scopes=target_scopes,
        )

        return creds

    def get_credentials(self, service_name: str) -> Any:
        """
        Smart resolver:
        - For 'sheets', 'docs', 'drive': tries Service Account first, falls back to OAuth 2.0.
        - For 'gmail', 'calendar': defaults to OAuth 2.0 (user context).
        """
        if service_name in ("sheets", "docs", "drive"):
            sa_creds = self.get_service_account_credentials(scopes=DEFAULT_SCOPES.get(service_name))
            if sa_creds:
                return sa_creds

        return self.get_oauth_credentials(service_name=service_name, scopes=None)
