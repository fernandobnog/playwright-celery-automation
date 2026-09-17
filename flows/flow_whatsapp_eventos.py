"""
Flow: Whatsapp Eventos (Reativação e Prospecção Comercial de Shows via WhatsApp).
Periodically checks the Eventos Google Sheet, identifies venues without upcoming gigs,
validates WhatsApp numbers via Evolution API, and dispatches customized, anti-spam paced messages.
"""

import asyncio
from datetime import datetime, date
import logging
import random
import time
from typing import Any, Dict, List, Optional

from core.celery_app import celery_app
from core.config import settings
from integrations.evolution import EvolutionClient, format_brazilian_phone
from integrations.google import GoogleHub
from storage.repository import repo

logger = logging.getLogger(__name__)

SPREADSHEET_ID = "1qNxFZgzQREMsPT0yDtqkvJ-qg8lrhbY0CP15nC4NdSc"
WORKSHEET_AGENDA = "Agenda"
WORKSHEET_LOCAL = "Local"


def parse_date_brazilian(date_str: Any) -> Optional[date]:
    """
    Parses 'dd/MM/yyyy' date strings.
    """
    if not date_str:
        return None
    raw = str(date_str).strip().split(" ")[0]
    try:
        return datetime.strptime(raw, "%d/%m/%Y").date()
    except ValueError:
        return None


def classify_venue_schedules(
    agenda_rows: List[Dict[str, Any]],
    reference_date: Optional[date] = None,
) -> Dict[str, Dict[str, bool]]:
    """
    Analyzes event history for each venue, mapping whether it has past or future events.
    """
    today = reference_date or date.today()
    schedule_map: Dict[str, Dict[str, bool]] = {}

    for event in agenda_rows:
        venue_name = str(event.get("Local") or "").strip()
        if not venue_name:
            continue
        key = venue_name.lower()
        event_date = parse_date_brazilian(event.get("Data"))
        if not event_date:
            continue

        if key not in schedule_map:
            schedule_map[key] = {"temPassado": False, "temFuturo": False}

        if event_date >= today:
            schedule_map[key]["temFuturo"] = True
        else:
            schedule_map[key]["temPassado"] = True

    return schedule_map


def build_engagement_message(status_agenda: str) -> str:
    """
    Builds personalized and polite engagement messages according to venue history.
    """
    if status_agenda == "Passado sem futuro":
        return (
            "Olá, tudo bem por aí? Aqui é o Fernando!\n\n"
            "Passando para dar um oi e ver como estão as coisas na casa! "
            "Estou montando a agenda dos próximos meses e seria um prazer enorme voltar a tocar pra vocês.\n\n"
            "O show segue naquela mesma pegada que vocês já conhecem: repertório de *MPB e Pop Rock*, "
            "volume na medida certa e aquele clima agradável para os clientes.\n\n"
            "Como estão os finais de semana de vocês? Bora encaixar uma nova data pra gente repetir a dose?\n\n"
            "Grande abraço e uma ótima semana!"
        )
    elif status_agenda == "Nunca teve evento":
        return (
            "Olá, tudo bem por aí? Aqui é o Fernando!\n\n"
            "Estou montando a agenda dos próximos meses com o show de voz e violão e gostaria muito de "
            "levar meu trabalho para o espaço de vocês.\n\n"
            "A proposta é um show focado em *MPB e Pop Rock*, com volume na medida certa e ambiente agradável "
            "para os clientes curtirem as mesas e conversarem tranquilamente.\n\n"
            "Como funciona a programação de música ao vivo de vocês nos finais de semana? "
            "Se tiverem interesse em encaixar uma data teste, fico à disposição.\n\n"
            "Grande abraço e uma ótima semana!"
        )
    else:  # "Tem no futuro"
        return (
            "Olá, tudo bem por aí? Aqui é o Fernando!\n\n"
            "Passando para dar um oi e dizer que estou bem animado para o nosso show! "
            "Aproveitando que estou organizando os próximos meses de agenda, queria ver como estão as coisas por aí.\n\n"
            "Já estou preparando o repertório (*MPB e Pop Rock*) pra garantir aquele som no volume ideal e "
            "criar o clima perfeito no dia do nosso evento.\n\n"
            "Se precisarem alinhar algum detalhe ou se já quiserem pensar em datas futuras para a sequência, "
            "fico à disposição.\n\n"
            "Grande abraço e uma ótima semana!"
        )


def process_and_dispatch_event_leads(
    spreadsheet_id: str = SPREADSHEET_ID,
    max_messages: int = 5,
    min_delay_seconds: float = 3.0,
    max_delay_seconds: float = 8.0,
    hub: Optional[GoogleHub] = None,
    evolution: Optional[EvolutionClient] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Core business logic: reads sheets, classifies venues, checks WhatsApp,
    applies anti-spam random delays, and records last contact dates.
    """
    google_hub = hub or GoogleHub()
    evo = evolution or EvolutionClient()

    logger.info("Starting Whatsapp Eventos prospecting flow...")

    # 1. Read Google Sheets
    agenda_rows = google_hub.sheets.read_records(spreadsheet_id, worksheet=WORKSHEET_AGENDA)
    local_rows = google_hub.sheets.read_records(spreadsheet_id, worksheet=WORKSHEET_LOCAL)

    schedule_map = classify_venue_schedules(agenda_rows)
    today_str = datetime.now().strftime("%d/%m/%Y")

    dispatched = []
    skipped = []

    for index, local in enumerate(local_rows):
        if len(dispatched) >= max_messages:
            logger.info("Reached maximum messages limit of %d per cycle.", max_messages)
            break

        venue_name = str(local.get("Local") or "").strip()
        if not venue_name:
            continue

        # Check MSG flag (must be "sim")
        msg_flag = str(local.get("MSG") or "").strip().lower()
        if msg_flag != "sim":
            skipped.append({"venue": venue_name, "reason": f"MSG flag is '{msg_flag}'"})
            continue

        # Check last contact date to prevent spamming within 20 days
        last_contact = parse_date_brazilian(local.get("Dia último Contato"))
        if last_contact and (date.today() - last_contact).days < 20:
            skipped.append({"venue": venue_name, "reason": "Already contacted recently (< 20 days)"})
            continue

        phone_raw = local.get("WhatsApp Principal") or local.get("WhatsApp Secundário")
        if not phone_raw:
            skipped.append({"venue": venue_name, "reason": "No phone number available"})
            continue

        try:
            phone = format_brazilian_phone(phone_raw)
        except ValueError as err:
            skipped.append({"venue": venue_name, "reason": f"Invalid phone: {err}"})
            continue

        # Determine agenda status
        venue_key = venue_name.lower()
        hist = schedule_map.get(venue_key)
        if hist:
            if hist["temFuturo"]:
                status_agenda = "Tem no futuro"
            elif hist["temPassado"]:
                status_agenda = "Passado sem futuro"
            else:
                status_agenda = "Nunca teve evento"
        else:
            status_agenda = "Nunca teve evento"

        # Verify WhatsApp existence
        try:
            has_whatsapp = asyncio.run(evo.check_whatsapp_number(phone))
        except Exception as check_err:
            logger.warning("Could not verify WhatsApp for %s: %s", phone, check_err)
            has_whatsapp = True  # Proceed cautiously if API check drops

        if not has_whatsapp:
            skipped.append({"venue": venue_name, "phone": phone, "reason": "Not registered on WhatsApp"})
            continue

        # Build personalized message
        message_text = build_engagement_message(status_agenda)

        if not dry_run:
            try:
                # Anti-spam jitter delay between messages
                delay = random.uniform(min_delay_seconds, max_delay_seconds)
                logger.info("Applying anti-spam delay of %.1f seconds before sending to %s...", delay, venue_name)
                time.sleep(delay)

                # Send message via Evolution API
                send_res = asyncio.run(evo.send_text_message(phone, message_text))
                logger.info("WhatsApp message sent to %s (%s): %s", venue_name, phone, send_res.get("status"))

                # Update sheet: row index is 2-indexed in Google Sheets (1 is header)
                sheet_row_index = index + 2
                # 'Dia último Contato' is typically column 6 (F)
                try:
                    google_hub.sheets.find_and_update_row(
                        spreadsheet_id=spreadsheet_id,
                        search_col=1,
                        search_value=venue_name,
                        update_col=6,
                        new_value=today_str,
                        worksheet=WORKSHEET_LOCAL,
                    )
                except Exception as update_err:
                    logger.warning("Could not update sheet for %s: %s", venue_name, update_err)

                dispatched.append({
                    "venue": venue_name,
                    "phone": phone,
                    "status_agenda": status_agenda,
                    "sent_at": datetime.now().isoformat(),
                })
            except Exception as send_err:
                logger.error("Failed to send WhatsApp message to %s: %s", venue_name, send_err)
                skipped.append({"venue": venue_name, "phone": phone, "reason": f"Send error: {send_err}"})
        else:
            dispatched.append({
                "venue": venue_name,
                "phone": phone,
                "status_agenda": status_agenda,
                "dry_run": True,
            })

    return {
        "status": "SUCCESS",
        "total_dispatched": len(dispatched),
        "total_skipped": len(skipped),
        "dispatched": dispatched,
        "skipped": skipped,
    }


@celery_app.task(name="flows.flow_whatsapp_eventos.task_reengajar_locais_eventos")
def task_reengajar_locais_eventos(max_messages: int = 5, dry_run: bool = False) -> Dict[str, Any]:
    """
    Celery task that automates re-engagement of venues with anti-spam pacing.
    """
    task_id = "whatsapp_eventos_" + datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    repo.log_flow_start(task_id, "whatsapp_eventos", {"max_messages": max_messages, "dry_run": dry_run})

    result = process_and_dispatch_event_leads(
        spreadsheet_id=SPREADSHEET_ID,
        max_messages=max_messages,
        dry_run=dry_run,
    )

    repo.log_flow_complete(task_id, result)
    return result
