"""
Flow: LinkedIn Connection Autopilot.
Autonomous prospecting and connection engine that extracts high-intent engagers
from target posts (LegalOps, LegalTech, AI, Software Engineering, HR), filters by ICP,
and dispatches humanized connection invitations within safe daily thresholds.
"""

from datetime import datetime
import logging
import random
import re
import time
from typing import Any, Dict, List, Optional

from core.celery_app import celery_app
from core.config import settings
from integrations.evolution import EvolutionClient
from scrapers.linkedin_publisher import linkedin_publisher
from storage.repository import repo

logger = logging.getLogger(__name__)

# Patterns identifying Ideal Customer Profile (ICP) and high-value peers
ICP_PATTERNS = [
    r"\blegalops\b",
    r"\blegal operations\b",
    r"jurídic",
    r"juridic",
    r"advogad",
    r"sóci",
    r"soci[oa]",
    r"\blawtech\b",
    r"\blegaltech\b",
    r"\bgeneral counsel\b",
    r"diretor",
    r"gerente",
    r"\bcto\b",
    r"\bcio\b",
    r"\bciso\b",
    r"\bhead\b",
    r"\btech lead\b",
    r"arquiteto de software",
    r"engenharia de software",
    r"desenvolvedor",
    r"inteligência artificial",
    r"inteligencia artificial",
    r"\bia\b",
    r"\bai\b",
    r"\bcompliance\b",
    r"\bdpo\b",
    r"\bdados\b",
    r"inovação",
    r"inovacao",
    r"\brh\b",
    r"\bpeople\b",
]

_COMPILED_ICP_REGEX = [re.compile(p, re.IGNORECASE) for p in ICP_PATTERNS]


def is_icp_prospect(headline: str) -> bool:
    """Checks if a prospect's headline matches high-value ICP keywords."""
    if not headline:
        return True  # If headline empty, allow inspection by default
    return any(p.search(headline) for p in _COMPILED_ICP_REGEX)


def generate_invite_note(nome: str, headline: str = "") -> str:
    """
    Generates a concise, warm, professional invitation note (< 270 chars).
    """
    primeiro_nome = nome.strip().split()[0] if nome else "colega"
    templates = [
        (
            f"Olá {primeiro_nome}! Acompanho discussões sobre tecnologia, IA e inovação corporativa "
            f"no dia a dia como CTO. Será um prazer conectar e trocar ideias por aqui!"
        ),
        (
            f"Olá {primeiro_nome}, vi sua atuação com tecnologia e gestão e achei muito alinhada "
            f"aos desafios que enfrentamos na ponta com engenharia e IA. Abraços e prazer em conectar!"
        ),
        (
            f"Olá {primeiro_nome}! Trocando experiências por aqui sobre automação, IA na prática e "
            f"operações corporativas. Conectando para acompanhar seus insights por aqui!"
        ),
    ]
    return random.choice(templates)[:285]


@celery_app.task(
    name="flows.flow_linkedin_autopilot.task_linkedin_autopilot_discovery",
    queue="scraping",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def task_linkedin_autopilot_discovery(self, batch_targets: int = 3) -> Dict[str, Any]:
    """
    Scrapes recent posts from target influencers to discover new active engager profiles.
    Filters by ICP and enqueues them in SQLite.
    """
    logger.info("Starting LinkedIn Autopilot Discovery (batch_targets=%d)...", batch_targets)
    targets = repo.get_active_linkedin_targets(limit=batch_targets)
    if not targets:
        return {"status": "NO_TARGETS", "discovered": 0}

    discovered_total = 0
    all_discovered = []

    for target in targets:
        target_nome = target["nome"]
        nicho = target["nicho"]
        profile_url = target["linkedin_url"]

        try:
            latest_post = linkedin_publisher.get_latest_post_from_profile(
                profile_url=profile_url,
                max_age_days=6.0,
                target_name=target_nome,
            )
            if not latest_post or not latest_post.get("post_url"):
                continue

            post_url = latest_post["post_url"]
            logger.info("Extracting engagers from post by %s: %s", target_nome, post_url)

            prospects = linkedin_publisher.extract_prospects_from_post(post_url=post_url, limit=12)
            for p in prospects:
                nome = p["nome"]
                headline = p["headline"]
                p_url = p["profile_url"]

                if is_icp_prospect(headline):
                    row_id = repo.save_autopilot_prospect(
                        nome=nome,
                        headline=headline,
                        profile_url=p_url,
                        source_target_nome=target_nome,
                        source_post_url=post_url,
                        nicho=nicho,
                    )
                    if row_id:
                        discovered_total += 1
                        all_discovered.append({"nome": nome, "headline": headline, "url": p_url})

            time.sleep(random.uniform(5.0, 10.0))

        except Exception as e:
            logger.error("Error discovering prospects on target %s: %s", target_nome, e, exc_info=True)

    logger.info("Autopilot Discovery finished: %d new ICP prospects saved.", discovered_total)
    return {
        "status": "COMPLETED",
        "discovered_count": discovered_total,
        "prospects": all_discovered,
    }


@celery_app.task(
    name="flows.flow_linkedin_autopilot.task_linkedin_autopilot_inviter",
    queue="scraping",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
)
def task_linkedin_autopilot_inviter(
    self,
    batch_size: int = 4,
    daily_limit: int = 15,
    use_note: bool = True,
) -> Dict[str, Any]:
    """
    Executes a safe batch of connection invitations to discovered prospects.
    Strictly enforces daily limits to preserve account health.
    """
    logger.info("Starting LinkedIn Autopilot Inviter (batch_size=%d, daily_limit=%d)...", batch_size, daily_limit)

    today_count = repo.get_daily_autopilot_invites_count()
    if today_count >= daily_limit:
        logger.info("Daily invite limit reached (%d/%d). Pausing invites until tomorrow.", today_count, daily_limit)
        return {"status": "DAILY_LIMIT_REACHED", "sent_today": today_count}

    remaining_quota = daily_limit - today_count
    actual_batch = min(batch_size, remaining_quota)

    pending_prospects = repo.get_pending_autopilot_prospects(limit=actual_batch)
    if not pending_prospects:
        logger.info("No pending prospects in queue to invite.")
        return {"status": "QUEUE_EMPTY", "processed": 0}

    results = []

    for prospect in pending_prospects:
        p_id = prospect["id"]
        nome = prospect["nome"]
        headline = prospect.get("headline") or ""
        p_url = prospect["profile_url"]

        note = generate_invite_note(nome=nome, headline=headline) if use_note else None

        try:
            logger.info("Sending invite to #%d %s (%s)...", p_id, nome, p_url)
            inv_res = linkedin_publisher.send_connection_invite(profile_url=p_url, note_text=note)
            inv_status = inv_res.get("status", "FAILED")

            repo.update_autopilot_prospect_status(
                prospect_id=p_id,
                status=inv_status,
                invite_note=note if inv_status == "INVITED" else None,
            )

            results.append({
                "id": p_id,
                "nome": nome,
                "profile_url": p_url,
                "status": inv_status,
            })

            # Random organic pause between profiles (18 to 35 seconds)
            time.sleep(random.uniform(18.0, 35.0))

        except Exception as err:
            logger.error("Failed to process prospect #%d (%s): %s", p_id, nome, err)
            repo.update_autopilot_prospect_status(prospect_id=p_id, status="FAILED")
            results.append({"id": p_id, "nome": nome, "status": "ERROR", "error": str(err)})

    # Notify WhatsApp with summary if invites were sent
    invited_successes = [r for r in results if r.get("status") == "INVITED"]
    whatsapp_recipient = getattr(settings, "EDITORIAL_WHATSAPP_RECIPIENT", None) or getattr(settings, "NOTIFICATION_PHONE", None)

    if invited_successes and whatsapp_recipient:
        try:
            ev = EvolutionClient()
            names_str = "\n".join([f"• *{r['nome']}* ({r['profile_url']})" for r in invited_successes])
            msg = (
                f"🤝 *AUTOPILOT LINKEDIN: CONVITES ENVIADOS*\n\n"
                f"Disparados *{len(invited_successes)} novos convites* qualificados de conexão:\n\n"
                f"{names_str}\n\n"
                f"📊 *Total enviado hoje:* {today_count + len(invited_successes)}/{daily_limit} convites."
            )
            ev.send_message(whatsapp_recipient, msg)
            logger.info("Dispatched WhatsApp summary for autopilot invites.")
        except Exception as e_wa:
            logger.warning("Could not dispatch WhatsApp autopilot notification: %s", e_wa)

    return {
        "status": "COMPLETED",
        "processed": len(results),
        "invited_count": len(invited_successes),
        "results": results,
    }
