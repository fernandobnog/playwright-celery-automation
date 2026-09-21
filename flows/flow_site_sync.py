"""
Flow: Site Data Synchronizer (Google Sheets -> PostgreSQL site-postgres).
Transfers synchronization of public shows agenda and music repertoire from Node.js
into robust, typed, asynchronous Celery tasks.
"""

from datetime import datetime, date, timezone
import json
import logging
import re
from typing import Any, Dict, List, Optional
import psycopg2
from psycopg2.extras import execute_values

from core.celery_app import celery_app
from core.config import settings
from integrations.google import GoogleHub
from storage.repository import repo

logger = logging.getLogger(__name__)

SPREADSHEET_AGENDA_ID = "1qNxFZgzQREMsPT0yDtqkvJ-qg8lrhbY0CP15nC4NdSc"
WORKSHEET_AGENDA_NAME = "Agenda"

SPREADSHEET_REPERTORIO_ID = "1XQJG42x2Fem9tBt_neKgbOwfdG6c_yhWftKu6d365N4"
WORKSHEET_REPERTORIO_NAME = "Lista de Musicas"


def get_venue_details(venue_name: str, obs: str = "") -> Dict[str, Optional[str]]:
    """
    Normalizes known venues across DDD 19 and São Paulo region.
    """
    v = (venue_name or "").lower()
    o = (obs or "").lower()

    if "zafferano" in v or "zafferano" in o:
        return {"city": "Mogi Mirim", "state": "SP", "address": "Zafferano Gastronomia"}
    if "areca" in v or "areca" in o:
        return {"city": "Mogi Mirim", "state": "SP", "address": "Areca Bambu Lounge"}
    if "gaúcho" in v or "gaucho" in v or "gaúcho" in o or "gaucho" in o:
        return {"city": "Mogi Mirim", "state": "SP", "address": "Gaúcho Prime Steakhouse"}
    if "tina" in v or "tina" in o:
        return {"city": "Mogi Mirim", "state": "SP", "address": "Bar do Tina"}
    if "holambra" in v or "holambra" in o:
        addr = "Conceito e Beleza Studio Hair" if ("conceito" in o or "beleza" in o or "carol" in o) else None
        return {"city": "Holambra", "state": "SP", "address": addr}
    if "mogi guaçu" in v or "mogi guacu" in v or "mogi guaçu" in o or "mogi guacu" in o:
        return {"city": "Mogi Guaçu", "state": "SP", "address": None}
    if "itapira" in v or "itapira" in o:
        return {"city": "Itapira", "state": "SP", "address": None}
    if "campinas" in v or "campinas" in o:
        return {"city": "Campinas", "state": "SP", "address": None}
    if "mogi mirim" in v or "mogi mirim" in o or "mogi" in v or "mogi" in o:
        return {"city": "Mogi Mirim", "state": "SP", "address": None}
    return {"city": "São Paulo (Região)", "state": "SP", "address": None}


def parse_date_brazilian(date_str: Any) -> Optional[datetime]:
    """
    Parses 'dd/MM/yyyy' date into Python datetime.
    """
    if not date_str:
        return None
    raw = str(date_str).strip().split(" ")[0]
    try:
        dt = datetime.strptime(raw, "%d/%m/%Y")
        return dt
    except ValueError:
        return None


def sync_agenda_to_postgres(
    spreadsheet_id: str = SPREADSHEET_AGENDA_ID,
    worksheet_name: str = WORKSHEET_AGENDA_NAME,
    postgres_url: Optional[str] = None,
    hub: Optional[GoogleHub] = None,
) -> Dict[str, Any]:
    """
    Reads Google Sheets Agenda and performs bulk upsert into PostgreSQL public."Show".
    """
    google_hub = hub or GoogleHub()
    db_url = postgres_url or settings.CHECK_LINKS_POSTGRES_URL

    logger.info("Starting Google Sheets -> PostgreSQL Agenda sync...")
    records = google_hub.sheets.read_records(spreadsheet_id, worksheet=worksheet_name)
    if not records:
        logger.warning("No records found in Agenda worksheet.")
        return {"status": "EMPTY", "count": 0}

    today = date.today()
    shows_to_upsert = []
    upcoming_count = 0

    for row in records:
        event_dt = parse_date_brazilian(row.get("Data"))
        venue = str(row.get("Local") or "").strip()
        if not event_dt or not venue:
            continue

        inicio = str(row.get("Início") or "").strip()
        termino = str(row.get("Término") or "").strip()
        obs = str(row.get("Observações") or "").strip()
        cache = str(row.get("Cache") or "").strip()

        details = get_venue_details(venue, obs)
        is_upcoming = event_dt.date() >= today
        if is_upcoming:
            upcoming_count += 1

        description_parts = []
        if inicio:
            description_parts.append(f"Horário: {inicio}" + (f" às {termino}" if termino else ""))
        if obs:
            description_parts.append(obs)
        if cache:
            description_parts.append(f"Cachê: {cache}")
        description = " | ".join(description_parts) if description_parts else None

        shows_to_upsert.append((
            event_dt,
            venue,
            venue[:50],  # venueShort
            details["city"],
            details["state"],
            details["address"],
            description,
            json.dumps(["Música ao Vivo", "Voz e Violão", details["city"]]),
            is_upcoming,
            True,  # active
            datetime.now(),
            datetime.now(),
        ))

    # Persist in PostgreSQL
    with psycopg2.connect(db_url) as conn:
        with conn.cursor() as cur:
            # We refresh upcoming status across all shows first
            cur.execute("""
                UPDATE public."Show"
                SET upcoming = (date >= CURRENT_DATE), "updatedAt" = NOW();
            """)

            # Upsert shows by matching venue and date::date
            for s in shows_to_upsert:
                cur.execute("""
                    SELECT id FROM public."Show" 
                    WHERE venue = %s AND date::date = %s::date
                    LIMIT 1;
                """, (s[1], s[0].date()))
                row = cur.fetchone()

                if row:
                    cur.execute("""
                        UPDATE public."Show"
                        SET "city" = %s, "state" = %s, "address" = %s, "description" = %s,
                            "tags" = %s, "upcoming" = %s, "active" = %s, "updatedAt" = NOW()
                        WHERE id = %s;
                    """, (s[3], s[4], s[5], s[6], s[7], s[8], s[9], row[0]))
                else:
                    cur.execute("""
                        INSERT INTO public."Show" (
                            "date", "venue", "venueShort", "city", "state", "address",
                            "description", "tags", "upcoming", "active", "createdAt", "updatedAt"
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """, s)

            # Insert SyncLog
            cur.execute("""
                INSERT INTO public."SyncLog" ("source", "status", "count", "message", "syncedAt")
                VALUES ('GOOGLE_SHEETS_AGENDA', 'SUCCESS', %s, %s, NOW());
            """, (len(shows_to_upsert), f"{len(shows_to_upsert)} shows sincronizados ({upcoming_count} futuros)"))
            conn.commit()

    logger.info("Successfully synced %d shows (%d upcoming) to PostgreSQL.", len(shows_to_upsert), upcoming_count)
    return {
        "status": "SUCCESS",
        "total_shows": len(shows_to_upsert),
        "upcoming_count": upcoming_count,
        "synced_at": datetime.now().isoformat(),
    }


def sync_repertorio_to_postgres(
    spreadsheet_id: str = SPREADSHEET_REPERTORIO_ID,
    worksheet_name: str = WORKSHEET_REPERTORIO_NAME,
    postgres_url: Optional[str] = None,
    hub: Optional[GoogleHub] = None,
) -> Dict[str, Any]:
    """
    Reads Google Sheets Lista de Músicas and syncs into PostgreSQL public."Song".
    """
    google_hub = hub or GoogleHub()
    db_url = postgres_url or settings.CHECK_LINKS_POSTGRES_URL

    logger.info("Starting Google Sheets -> PostgreSQL Repertoire sync...")
    records = google_hub.sheets.read_records(spreadsheet_id, worksheet=worksheet_name)
    if not records:
        logger.warning("No records found in Repertoire worksheet.")
        return {"status": "EMPTY", "count": 0}

    songs_to_upsert = []
    for r in records:
        # Find key columns dynamically
        title = None
        artist = None
        tom = None
        bpm = None
        estilo = "Outro"
        vs = None

        for k, v in r.items():
            k_clean = str(k).strip().lower()
            val_clean = str(v).strip() if v is not None else ""
            if "música" in k_clean or "musica" in k_clean or "título" in k_clean or "titulo" in k_clean:
                title = val_clean
            elif "artista" in k_clean or "cantor" in k_clean or "banda" in k_clean:
                artist = val_clean
            elif k_clean == "tom":
                tom = val_clean or None
            elif k_clean == "bpm":
                try:
                    bpm = int(re.sub(r"\D", "", val_clean)) if val_clean else None
                except ValueError:
                    bpm = None
            elif "estilo" in k_clean or "gênero" in k_clean or "genero" in k_clean:
                estilo = val_clean or "Outro"
            elif k_clean == "vs":
                vs = val_clean or None

        if not title or not artist:
            continue

        songs_to_upsert.append((
            title,
            artist,
            tom,
            bpm,
            estilo,
            vs,
            True,  # active
            datetime.now(),
            datetime.now(),
        ))

    # Bulk upsert in PostgreSQL
    updated_count = 0
    inserted_count = 0
    with psycopg2.connect(db_url) as conn:
        with conn.cursor() as cur:
            for s in songs_to_upsert:
                cur.execute("""
                    SELECT id FROM public."Song"
                    WHERE LOWER(title) = LOWER(%s) AND LOWER(artist) = LOWER(%s)
                    LIMIT 1;
                """, (s[0], s[1]))
                row = cur.fetchone()

                if row:
                    updated_count += 1
                    cur.execute("""
                        UPDATE public."Song"
                        SET "tom" = %s, "bpm" = %s, "estilo" = %s, "vs" = %s, "active" = %s, "updatedAt" = NOW()
                        WHERE id = %s;
                    """, (s[2], s[3], s[4], s[5], s[6], row[0]))
                else:
                    inserted_count += 1
                    cur.execute("""
                        INSERT INTO public."Song" (
                            "title", "artist", "tom", "bpm", "estilo", "vs", "active", "createdAt", "updatedAt"
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """, s)

            # Insert SyncLog
            cur.execute("""
                INSERT INTO public."SyncLog" ("source", "status", "count", "message", "syncedAt")
                VALUES ('GOOGLE_SHEETS_REPERTORIO', 'SUCCESS', %s, %s, NOW());
            """, (len(songs_to_upsert), f"{len(songs_to_upsert)} músicas sincronizadas ({inserted_count} novas, {updated_count} atualizadas)"))
            conn.commit()

    logger.info("Successfully synced %d songs (%d new, %d updated) to PostgreSQL.", len(songs_to_upsert), inserted_count, updated_count)
    return {
        "status": "SUCCESS",
        "total_songs": len(songs_to_upsert),
        "inserted": inserted_count,
        "updated": updated_count,
        "synced_at": datetime.now().isoformat(),
    }


@celery_app.task(name="flows.flow_site_sync.task_sync_site_agenda")
def task_sync_site_agenda() -> Dict[str, Any]:
    """Celery periodic task for syncing shows to PostgreSQL."""
    task_id = "site_agenda_sync_" + datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    repo.log_flow_start(task_id, "sync_site_agenda", {})
    try:
        res = sync_agenda_to_postgres()
        repo.log_flow_complete(task_id, res)
        return res
    except Exception as e:
        logger.error("Error executing task_sync_site_agenda: %s", e, exc_info=True)
        repo.log_flow_error(task_id, str(e))
        raise


@celery_app.task(name="flows.flow_site_sync.task_sync_site_repertorio")
def task_sync_site_repertorio() -> Dict[str, Any]:
    """Celery periodic task for syncing repertoire to PostgreSQL."""
    task_id = "site_repertorio_sync_" + datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    repo.log_flow_start(task_id, "sync_site_repertorio", {})
    try:
        res = sync_repertorio_to_postgres()
        repo.log_flow_complete(task_id, res)
        return res
    except Exception as e:
        logger.error("Error executing task_sync_site_repertorio: %s", e, exc_info=True)
        repo.log_flow_error(task_id, str(e))
        raise
