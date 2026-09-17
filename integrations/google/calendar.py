"""
Google Calendar API Service Wrapper.
Provides event querying, conflict detection, event creation with custom reminders,
and deletion capabilities.
"""

import logging
from typing import Any, Dict, List, Optional
from googleapiclient.discovery import build

from integrations.google.auth import GoogleAuthManager

logger = logging.getLogger(__name__)


class CalendarService:
    """
    Client for Google Calendar API operations.
    """

    def __init__(self, auth_manager: Optional[GoogleAuthManager] = None):
        self.auth = auth_manager or GoogleAuthManager()

    def _get_service(self):
        creds = self.auth.get_credentials("calendar")
        return build("calendar", "v3", credentials=creds)

    def list_events(
        self,
        calendar_id: str,
        time_min: str,
        time_max: str,
        single_events: bool = True,
        order_by: str = "startTime",
    ) -> List[Dict[str, Any]]:
        """
        Lists events in a given time interval.
        """
        service = self._get_service()
        events_result = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=single_events,
                orderBy=order_by,
            )
            .execute()
        )
        return events_result.get("items", [])

    def create_event(
        self,
        calendar_id: str,
        summary: str,
        description: str,
        start_iso: str,
        end_iso: str,
        popup_reminder_minutes: Optional[List[int]] = None,
        time_zone: str = "America/Sao_Paulo",
    ) -> Dict[str, Any]:
        """
        Inserts a new event into the calendar with reminder preferences.
        """
        service = self._get_service()

        reminders = {"useDefault": True}
        if popup_reminder_minutes:
            reminders = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": m} for m in popup_reminder_minutes],
            }

        event_body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_iso, "timeZone": time_zone},
            "end": {"dateTime": end_iso, "timeZone": time_zone},
            "reminders": reminders,
        }

        created = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        logger.info(
            "Created calendar event '%s' on %s (ID: %s)",
            summary,
            start_iso,
            created.get("id"),
        )
        return created

    def delete_event(self, calendar_id: str, event_id: str) -> bool:
        """
        Removes an event by ID.
        """
        service = self._get_service()
        try:
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
            logger.info("Deleted calendar event %s from %s", event_id, calendar_id)
            return True
        except Exception as e:
            logger.error("Failed to delete calendar event %s: %s", event_id, e)
            return False
