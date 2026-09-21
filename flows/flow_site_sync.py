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
from integrations.site_publisher import site_publisher
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


def parse_date_brazilian(date_str: Any, time_str: Any = "20:00") -> Optional[datetime]:
    """
    Parses 'dd/MM/yyyy' date and 'HH:mm' time in America/Sao_Paulo (-03:00),
    returning naive UTC datetime for PostgreSQL timestamp without time zone storage.
    """
    if not date_str:
        return None
    raw = str(date_str).strip().split(" ")[0]
    parts = raw.split("/")
    if len(parts) != 3:
        return None
    try:
        day = int(parts[0])
        month = int(parts[1])
        year = int(parts[2])
        if year < 100:
            year += 2000
    except ValueError:
        return None

    hour, minute = 20, 0
    if time_str:
        t_clean = str(time_str).strip()
        t_parts = t_clean.split(":")
        if len(t_parts) >= 2:
            try:
                hour = int(t_parts[0])
                minute = int(t_parts[1])
            except ValueError:
                pass
        elif len(t_parts) == 1 and t_parts[0].isdigit():
            try:
                hour = int(t_parts[0])
            except ValueError:
                pass

    try:
        from zoneinfo import ZoneInfo
        tz_brt = ZoneInfo("America/Sao_Paulo")
        dt_brt = datetime(year, month, day, hour, minute, tzinfo=tz_brt)
        dt_utc = dt_brt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        return dt_utc
    except Exception:
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
    from zoneinfo import ZoneInfo
    google_hub = hub or GoogleHub()
    db_url = postgres_url or settings.CHECK_LINKS_POSTGRES_URL

    logger.info("Starting Google Sheets -> PostgreSQL Agenda sync...")
    records = google_hub.sheets.read_records(spreadsheet_id, worksheet=worksheet_name)
    if not records:
        logger.warning("No records found in Agenda worksheet.")
        return {"status": "EMPTY", "count": 0}

    today_brt = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    shows_to_upsert = []
    upcoming_count = 0

    for row in records:
        raw_venue = str(row.get("Local") or "").strip()
        date_str = str(row.get("Data") or "").strip()
        inicio = str(row.get("Início") or "").strip()
        obs = str(row.get("Observações") or "").strip()

        event_dt_utc = parse_date_brazilian(date_str, inicio)
        if not event_dt_utc or not raw_venue:
            continue

        event_dt_brt = event_dt_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/Sao_Paulo"))
        is_upcoming = event_dt_brt.date() >= today_brt
        if is_upcoming:
            upcoming_count += 1

        is_private = "privad" in raw_venue.lower() or "privad" in obs.lower()
        venue = "Evento Privado" if is_private else raw_venue
        venue_short = "Privado" if is_private else (raw_venue.split()[0] if raw_venue else raw_venue)
        details = get_venue_details(raw_venue, obs)

        description = (
            "Apresentação exclusiva de voz e violão para evento privado. Repertório personalizado com clássicos de MPB, Pop Rock acústico e canções selecionadas."
            if is_private
            else "Show especial de voz e violão ao vivo. Clássicos de MPB, Pop Rock acústico e canções autorais."
        )

        tags = (
            ["Voz & Violão", "Evento Privado", "MPB", "Pop Rock Acústico"]
            if is_private
            else ["Voz & Violão", "MPB", "Pop Rock Acústico"]
        )

        shows_to_upsert.append({
            "dt_utc": event_dt_utc,
            "dt_brt_date": event_dt_brt.date(),
            "venue": venue,
            "venueShort": venue_short,
            "city": details["city"],
            "state": details["state"],
            "address": details["address"],
            "description": description,
            "tags": json.dumps(tags),
            "upcoming": is_upcoming,
            "active": True,
        })

    # Persist in PostgreSQL
    touched_ids = []
    with psycopg2.connect(db_url) as conn:
        with conn.cursor() as cur:
            for s in shows_to_upsert:
                cur.execute("""
                    SELECT id FROM public."Show"
                    WHERE (venue = %s OR (venue IN ('Privado', 'Evento Privado') AND %s IN ('Privado', 'Evento Privado')))
                      AND (date AT TIME ZONE 'UTC' AT TIME ZONE 'America/Sao_Paulo')::date = %s::date
                    LIMIT 1;
                """, (s["venue"], s["venue"], s["dt_brt_date"]))
                row = cur.fetchone()

                if row:
                    show_id = row[0]
                    touched_ids.append(show_id)
                    cur.execute("""
                        UPDATE public."Show"
                        SET "date" = %s, "venue" = %s, "venueShort" = %s, "city" = %s, "state" = %s,
                            "address" = %s, "description" = %s, "tags" = %s, "upcoming" = %s,
                            "active" = %s, "updatedAt" = NOW()
                        WHERE id = %s;
                    """, (
                        s["dt_utc"], s["venue"], s["venueShort"], s["city"], s["state"],
                        s["address"], s["description"], s["tags"], s["upcoming"],
                        s["active"], show_id
                    ))
                else:
                    cur.execute("""
                        INSERT INTO public."Show" (
                            "date", "venue", "venueShort", "city", "state", "address",
                            "description", "tags", "upcoming", "active", "createdAt", "updatedAt"
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                        RETURNING id;
                    """, (
                        s["dt_utc"], s["venue"], s["venueShort"], s["city"], s["state"],
                        s["address"], s["description"], s["tags"], s["upcoming"], s["active"]
                    ))
                    new_id = cur.fetchone()[0]
                    touched_ids.append(new_id)

            if touched_ids:
                cur.execute("""
                    DELETE FROM public."Show"
                    WHERE id NOT IN %s;
                """, (tuple(touched_ids),))

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
        rebuild_res = site_publisher.trigger_static_rebuild()
        res["rebuild"] = rebuild_res
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
        rebuild_res = site_publisher.trigger_static_rebuild()
        res["rebuild"] = rebuild_res
        repo.log_flow_complete(task_id, res)
        return res
    except Exception as e:
        logger.error("Error executing task_sync_site_repertorio: %s", e, exc_info=True)
        repo.log_flow_error(task_id, str(e))
        raise
