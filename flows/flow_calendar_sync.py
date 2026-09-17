"""
Flow: Add evento na agenda (Google Sheets -> Google Calendar Sync).
Periodically synchronizes upcoming musical events from Google Sheets to Google Calendar
with conflict detection and automated 24h / 36h reminders.
"""

from datetime import datetime, date
import logging
import re
from typing import Any, Dict, List, Optional
from celery import shared_task

from core.celery_app import celery_app
from core.config import settings
from integrations.google_service import GoogleServicesClient
from storage.repository import repo

logger = logging.getLogger(__name__)

SPREADSHEET_ID = "1qNxFZgzQREMsPT0yDtqkvJ-qg8lrhbY0CP15nC4NdSc"
WORKSHEET_NAME = "Agenda"
CALENDAR_ID = settings.GOOGLE_CALENDAR_ID or "2ef2cov1mufpls2idco4svqj3s@group.calendar.google.com"

TIME_REGEX = re.compile(r"^\d{2}:\d{2}$")


def parse_brazilian_date(date_str: Any) -> Optional[date]:
    """
    Parses date string in 'dd/MM/yyyy' format.
    """
    if not date_str:
        return None
    cleaned = str(date_str).strip().split(" ")[0]
    try:
        return datetime.strptime(cleaned, "%d/%m/%Y").date()
    except ValueError:
        return None


def format_event_summary(local: str, cache: str, inicio_valido: bool, termino_valido: bool) -> str:
    loc = (local or "").strip()
    cch = (cache or "").strip()
    if not inicio_valido or not termino_valido:
        return f"Tocar-Negociar Hora-{loc}-{cch}"
    return f"Tocar-{loc}-{cch}"


def filter_future_sheet_events(rows: List[Dict[str, Any]], reference_date: Optional[date] = None) -> List[Dict[str, Any]]:
    """
    Filters events having date strictly after today.
    """
    ref = reference_date or date.today()
    future_events = []

    for row in rows:
        event_date = parse_brazilian_date(row.get("Data"))
        if event_date and event_date > ref:
            row["_parsed_date"] = event_date
            future_events.append(row)

    return future_events


@celery_app.task(name="flows.flow_calendar_sync.task_sync_sheets_to_calendar")
def task_sync_sheets_to_calendar() -> Dict[str, Any]:
    """
    Periodic task running every 30 minutes to synchronize upcoming events into Google Calendar.
    """
    task_id = "calendar_sync_" + datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    logger.info("Starting Google Sheets -> Google Calendar synchronization...")
    repo.log_flow_start(task_id, "calendar_sync", {})

    google_svc = GoogleServicesClient()

    # 1. Read sheet records
    try:
        records = google_svc.read_spreadsheet_records(
            spreadsheet_id=SPREADSHEET_ID,
            worksheet_title_or_index=WORKSHEET_NAME,
        )
    except Exception as read_err:
        logger.error("Failed to read Google Sheets: %s", read_err)
        return {"status": "ERROR", "message": str(read_err)}

    # 2. Filter future events
    future_events = filter_future_sheet_events(records)
    logger.info("Found %d future events in Google Sheets.", len(future_events))

    created_events = []
    skipped_events = []

    for item in future_events:
        event_date = item["_parsed_date"]
        inicio = str(item.get("Início") or "").strip()
        termino = str(item.get("Término") or "").strip()

        inicio_valido = bool(TIME_REGEX.match(inicio))
        termino_valido = bool(TIME_REGEX.match(termino))

        start_time_str = inicio if inicio_valido else "04:00"
        end_time_str = termino if termino_valido else "05:00"

        start_iso = f"{event_date.isoformat()}T{start_time_str}:00-03:00"
        end_iso = f"{event_date.isoformat()}T{end_time_str}:00-03:00"

        # 3. Check for existing event in Google Calendar
        try:
            existing = google_svc.list_calendar_events(
                calendar_id=CALENDAR_ID,
                time_min=start_iso,
                time_max=end_iso,
            )
        except Exception as cal_err:
            logger.warning("Error checking calendar for %s: %s", start_iso, cal_err)
            existing = []

        if not existing:
            summary = format_event_summary(
                local=item.get("Local", ""),
                cache=item.get("Cache", ""),
                inicio_valido=inicio_valido,
                termino_valido=termino_valido,
            )
            description = item.get("Observações", "")

            try:
                new_event = google_svc.create_calendar_event(
                    calendar_id=CALENDAR_ID,
                    summary=summary,
                    description=description,
                    start_iso=start_iso,
                    end_iso=end_iso,
                    popup_reminder_minutes=[1440, 2160],  # 24h and 36h
                )
                created_events.append({"summary": summary, "id": new_event.get("id")})
                logger.info("Created calendar event: %s (%s)", summary, start_iso)
            except Exception as create_err:
                logger.error("Failed to create calendar event %s: %s", summary, create_err)
        else:
            skipped_events.append({"summary": existing[0].get("summary"), "date": start_iso})

    final_result = {
        "status": "SUCCESS",
        "total_future_rows": len(future_events),
        "created_count": len(created_events),
        "skipped_count": len(skipped_events),
        "created_events": created_events,
    }
    repo.log_flow_complete(task_id, final_result)
    return final_result
