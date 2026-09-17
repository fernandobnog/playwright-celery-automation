"""
Flow: Check Links NT (Network Connectivity Monitoring).
Periodically checks link availability (ICMP ping) and notifies via WhatsApp upon status change.
"""

import logging
import re
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional
from core.celery_app import celery_app
from core.config import settings
from integrations.evolution import EvolutionClient
from storage.repository import repo

logger = logging.getLogger(__name__)

DEFAULT_LINKS = [
    {"name": "VIVO", "ip": "186.233.20.148", "local": "Nogueira e Tognin"},
    {"name": "NEOLINK", "ip": "187.9.8.226", "local": "Nogueira e Tognin"},
]


def ping_ip(ip: str, count: int = 2, timeout_seconds: int = 2) -> Dict[str, Any]:
    """
    Executes an ICMP ping to the target IP and parses the result.
    """
    try:
        res = subprocess.run(
            ["ping", "-c", str(count), "-W", str(timeout_seconds), ip],
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 3,
        )
        if res.returncode == 0:
            avg_match = re.search(r"=\s*[\d\.]+/([\d\.]+)/[\d\.]+", res.stdout)
            latency = f"{avg_match.group(1)} ms" if avg_match else "OK"
            return {"status": "UP", "latency": latency, "error": None}
        else:
            return {"status": "DOWN", "latency": None, "error": res.stderr.strip() or "Packet loss"}
    except Exception as e:
        return {"status": "DOWN", "latency": None, "error": str(e)}


def get_stored_link_statuses(postgres_url: Optional[str] = None) -> Dict[str, str]:
    """
    Retrieves previous link statuses from PostgreSQL if available.
    """
    url = postgres_url or settings.CHECK_LINKS_POSTGRES_URL
    if not url:
        return {}

    try:
        import psycopg2
        with psycopg2.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT link, status FROM producao.check_links;")
                rows = cur.fetchall()
                return {row[0]: row[1] for row in rows}
    except Exception as e:
        logger.warning("Could not query PostgreSQL for old link status: %s", e)
        return {}


def update_link_status_postgres(link_name: str, new_status: str, postgres_url: Optional[str] = None) -> bool:
    """
    Updates the link status in PostgreSQL table producao.check_links.
    """
    url = postgres_url or settings.CHECK_LINKS_POSTGRES_URL
    if not url:
        return False

    try:
        import psycopg2
        with psycopg2.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE producao.check_links
                    SET status = %s, ultima_modificacao = NOW()
                    WHERE link = %s;
                    """,
                    (new_status, link_name),
                )
                conn.commit()
                return True
    except Exception as e:
        logger.error("Failed to update PostgreSQL link status for %s: %s", link_name, e)
        return False


@celery_app.task(name="flows.flow_link_monitor.task_check_network_links")
def task_check_network_links() -> Dict[str, Any]:
    """
    Celery task that tests all monitored links and alerts on transitions.
    """
    logger.info("Executing link connectivity health check...")
    old_statuses = repo.get_link_statuses()
    if not old_statuses:
        pg_statuses = get_stored_link_statuses()
        if pg_statuses:
            old_statuses = pg_statuses
            for k, v in pg_statuses.items():
                repo.update_link_status(k, v)

    results = []
    evo = EvolutionClient()
    import asyncio

    for link in DEFAULT_LINKS:
        name = link["name"]
        ip = link["ip"]

        ping_result = ping_ip(ip)
        current_status = ping_result["status"]
        prev_status = old_statuses.get(name)

        # First run: seed baseline without firing false alert
        if prev_status is None:
            logger.info("Seeding initial baseline status for link %s: %s", name, current_status)
            repo.update_link_status(name, current_status, ping_result["latency"])
            update_link_status_postgres(name, current_status)
            status_changed = False
        else:
            status_changed = prev_status != current_status

        results.append({
            "name": name,
            "ip": ip,
            "status": current_status,
            "previous_status": prev_status,
            "changed": status_changed,
            "latency": ping_result["latency"],
            "error": ping_result["error"],
            "checked_at": datetime.utcnow().isoformat(),
        })

        if status_changed:
            logger.warning(
                "Link %s status changed from %s to %s! State transition, sending alert...",
                name,
                prev_status,
                current_status,
            )
            # 1. Update SQLite and PostgreSQL
            repo.update_link_status(name, current_status, ping_result["latency"])
            update_link_status_postgres(name, current_status)

            # 2. Dispatch WhatsApp Notification
            notification_text = f"O Link {name} está {current_status} no NT."
            try:
                asyncio.run(
                    evo.send_text_message(
                        phone=settings.NOTIFICATION_PHONE,
                        text=notification_text,
                    )
                )
                logger.info("Alert WhatsApp dispatched successfully to %s", settings.NOTIFICATION_PHONE)
            except Exception as notify_err:
                logger.error("Failed to dispatch WhatsApp alert for %s: %s", name, notify_err)
        else:
            # Refresh last_checked timestamp
            repo.update_link_status(name, current_status, ping_result["latency"])

    return {"status": "SUCCESS", "links_checked": len(results), "details": results}
