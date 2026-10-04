"""
Flow: LinkedIn Sniper Growth & Targeted Engagement.
Autonomous radar that monitors top-tier influencers across TI Jurídico, Advocacia, and RH,
synthesizes expert-grade practitioner comments using Gemini, and executes human-in-the-loop
approvals via WhatsApp (Evolution API) and Playwright.
"""

from datetime import datetime
import html
import logging
import random
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from core.celery_app import celery_app
from core.config import settings
from core.security import create_linkedin_comment_action_token
from integrations.evolution import EvolutionClient
from integrations.gemini import GeminiClient
from scrapers.linkedin_publisher import linkedin_publisher
from storage.repository import repo

logger = logging.getLogger(__name__)


class SniperCommentResult(BaseModel):
    comentario: str = Field(
        description=(
            "Comentário de 3 a 5 parágrafos curtos (4 a 6 linhas no total), afiado, técnico, "
            "sem clichês de IA, agregando valor ou contraponto de bancada e terminando com uma reflexão provocativa."
        )
    )
    tese_central: str = Field(description="Resumo em 1 frase da tese do post analisado")
    angulo_utilizado: str = Field(description="Qual ângulo foi explorado: contraponto técnico, caso real ou pergunta instigante")


SNIPER_COMMENT_SYSTEM_INSTRUCTION = """
Você é Fernando Nogueira, Arquiteto de Soluções, Engenheiro de Software e especialista em IA e Governança Técnica (fernandonogueira.dev.br).
Você está redigindo um comentário especializado de alto impacto para ser publicado em um post de LinkedIn de um líder influente (RH, Advocacia ou TI Jurídico).

Objetivo inegociável do comentário:
1. Ser o "TOP COMMENT" da thread: o comentário mais lúcido, técnico, instigante e bem fundamentado da publicação.
2. Extensão exata: 4 a 6 linhas no total, com quebras de linha para leitura limpa e dinâmica no mobile.
3. NUNCA faça elogio vazio ("Parabéns pelo post!", "Muito bom!", "Concordo plenamente!"). Comece direto na tese, no contraponto ou no dado de bastidor.
4. Traga a perspectiva da engenharia e da trincheira:
   - Se o post for de TI Jurídico / LegalOps: fale sobre débito técnico, riscos de automações frágeis, limites reais de LLMs vs determinismo, integridade de dados e auditoria.
   - Se o post for de Advocacia / Direito Digital: fale sobre governança de dados, auditoria de prompts, vazamentos internos e o perigo do "achismo técnico".
   - Se o post for de RH / Gestão de Pessoas: fale sobre a armadilha de querer substituir pessoas por ferramentas de IA sem preparo cultural, ou o desafio de manter times seniores engajados.
5. FILTRO ANTI-CLICHÊS ESTRITO: É expressamente proibido usar jargões de IA como "no mundo de hoje", "cenário atual", "é fundamental", "divisor de águas", "mergulhar fundo". Fale como um engenheiro/arquiteto que lida com código, bancos de dados e servidores de verdade.
6. Fechamento: Conclua com uma pergunta reflexiva ou ponderação que faça o autor do post ou a audiência querer te responder imediatamente.
"""


def generate_sniper_comment(
    post_text: str,
    author_name: str,
    nicho: str,
    gemini_client: Optional[GeminiClient] = None,
) -> SniperCommentResult:
    """
    Synthesizes a high-impact, expert sniper comment using Gemini.
    """
    gemini = gemini_client or GeminiClient()

    prompt = (
        f"Analise a publicação recente no LinkedIn do autor '{author_name}' (Nicho: {nicho}):\n\n"
        f"--- CONTEÚDO DO POST ---\n"
        f"{post_text[:2500]}\n"
        f"-------------------------\n\n"
        "Redija o comentário especializado 'Sniper' seguindo à risca as diretrizes da persona Fernando Nogueira. "
        "O comentário deve soar 100% humano, técnico, assertivo e com alto potencial de gerar debate nos comentários."
    )

    result: SniperCommentResult = gemini.generate_structured(
        prompt=prompt,
        system_instruction=SNIPER_COMMENT_SYSTEM_INSTRUCTION,
        response_model=SniperCommentResult,
        model_name="gemini-2.5-flash",
    )
    return result


@celery_app.task(
    name="flows.flow_linkedin_growth.task_linkedin_sniper_radar",
    queue="flows",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def task_linkedin_sniper_radar(self, batch_size: int = 4) -> Dict[str, Any]:
    """
    Periodic radar task:
    1. Selects a small batch of target profiles from SQLite.
    2. Uses Playwright to inspect recent activity on each target.
    3. If a fresh post is found and not yet reviewed:
       - Generates an expert sniper comment via Gemini.
       - Stores draft in database as PENDING_APPROVAL.
       - Sends notification with 1-click approval link to WhatsApp.
    """
    logger.info("Starting LinkedIn Sniper Radar execution (batch_size=%d)...", batch_size)
    targets = repo.get_active_linkedin_targets(limit=batch_size)
    if not targets:
        logger.info("No active LinkedIn targets found in database.")
        return {"status": "NO_TARGETS", "processed": 0}

    results = []
    gemini = GeminiClient()
    evolution = EvolutionClient()
    whatsapp_recipient = settings.EDITORIAL_WHATSAPP_RECIPIENT or settings.NOTIFICATION_PHONE

    for target in targets:
        target_id = target["id"]
        target_nome = target["nome"]
        nicho = target["nicho"]
        profile_url = target["linkedin_url"]
        last_seen_post_id = target.get("ultimo_post_id")

        logger.info("Inspecting target %s (%s)...", target_nome, profile_url)

        try:
            latest_post = linkedin_publisher.get_latest_post_from_profile(profile_url)
            # Update check timestamp
            post_id = latest_post.get("post_id") if latest_post else None
            repo.update_target_last_check(target_id, post_id or last_seen_post_id)

            if not latest_post or not latest_post.get("text"):
                logger.info("No new content found for target %s.", target_nome)
                continue

            post_url = latest_post.get("post_url") or profile_url
            post_text = latest_post["text"]
            post_author = latest_post.get("author") or target_nome
            time_text = latest_post.get("time_text", "")

            # Check if this post ID was already checked or exists in comments
            existing_comment = repo.get_connection().execute(
                "SELECT id FROM linkedin_growth_comments WHERE post_url = ?",
                (post_url,),
            ).fetchone() if hasattr(repo, "get_connection") else None

            if not existing_comment:
                with repo._get_connection() as conn:
                    existing_comment = conn.execute(
                        "SELECT id FROM linkedin_growth_comments WHERE post_url = ?",
                        (post_url,),
                    ).fetchone()

            if existing_comment:
                logger.info("Post %s from %s already processed. Skipping.", post_url, target_nome)
                continue

            logger.info("New post detected for %s! Generating sniper comment...", target_nome)
            comment_res = generate_sniper_comment(
                post_text=post_text,
                author_name=post_author,
                nicho=nicho,
                gemini_client=gemini,
            )

            # Save draft in SQLite
            comment_id = repo.save_linkedin_growth_comment(
                target_id=target_id,
                target_nome=target_nome,
                post_url=post_url,
                post_texto=post_text[:1500],
                post_autor=post_author,
                comentario_gerado=comment_res.comentario,
            )

            # Generate secure approval token
            action_token = create_linkedin_comment_action_token(
                comment_id=comment_id,
                post_url=post_url,
                target_nome=target_nome,
            )

            # Update token on record
            with repo._get_connection() as conn:
                conn.execute(
                    "UPDATE linkedin_growth_comments SET action_token = ? WHERE id = ?",
                    (action_token, comment_id),
                )
                conn.commit()

            # Format approval URL
            approve_url = f"https://fernandonogueira.dev.br/api/v1/editorial/linkedin-comment/approve?token={action_token}"

            # Dispatch notification to WhatsApp
            snippet = post_text[:280].replace("\n", " ").strip()
            wa_text = (
                f"🎯 *SNIPER LINKEDIN: NOVO POST DETECTADO*\n\n"
                f"👤 *Autor:* {target_nome} [{nicho}]\n"
                f"⏱️ *Publicado há:* {time_text or 'recente'}\n"
                f"🔗 *Post Original:* {post_url}\n\n"
                f"📝 *Trecho do Post:*\n\"{snippet}...\"\n\n"
                f"💡 *Sugestão de Comentário Especialista:*\n\"{comment_res.comentario}\"\n\n"
                f"👉 *Para aprovar e postar agora com 1 clique:*\n{approve_url}"
            )

            if whatsapp_recipient:
                try:
                    evolution.send_message(whatsapp_recipient, wa_text)
                    logger.info("Dispatched sniper comment approval alert via WhatsApp to %s", whatsapp_recipient)
                except Exception as err_wa:
                    logger.warning("Failed to send WhatsApp alert for sniper comment: %s", err_wa)

            results.append({
                "target": target_nome,
                "post_url": post_url,
                "comment_id": comment_id,
                "status": "ALERTED",
            })

            # Random sleep between target lookups to maintain organic browsing profile
            time.sleep(random.uniform(5.0, 10.0))

        except Exception as e:
            logger.error("Error processing target %s: %s", target_nome, e, exc_info=True)

    return {
        "status": "COMPLETED",
        "processed_targets": len(targets),
        "alerts_created": len(results),
        "results": results,
    }


@celery_app.task(
    name="flows.flow_linkedin_growth.task_publish_approved_linkedin_comment",
    queue="scraping",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def task_publish_approved_linkedin_comment(self, comment_id: int) -> Dict[str, Any]:
    """
    Executes publication of an approved sniper comment via Playwright.
    """
    logger.info("Executing publication of approved LinkedIn sniper comment #%d...", comment_id)
    comment = repo.get_linkedin_growth_comment_by_id(comment_id)
    if not comment:
        raise ValueError(f"Comment record #{comment_id} not found in database.")

    if comment["status"] == "PUBLISHED":
        logger.info("Comment #%d already published at %s. Skipping.", comment_id, comment.get("published_at"))
        return {"status": "ALREADY_PUBLISHED", "comment_id": comment_id}

    post_url = comment["post_url"]
    comentario = comment["comentario_gerado"]
    target_nome = comment["target_nome"]

    # Execute publication with humanized typing
    pub_res = linkedin_publisher.comment_on_post(
        post_url=post_url,
        comment_text=comentario,
    )

    # Mark as published in repository
    repo.update_linkedin_growth_comment_status(comment_id=comment_id, status="PUBLISHED")

    # Send confirmation via WhatsApp
    whatsapp_recipient = settings.EDITORIAL_WHATSAPP_RECIPIENT or settings.NOTIFICATION_PHONE
    if whatsapp_recipient:
        try:
            ev = EvolutionClient()
            confirm_msg = (
                f"✅ *COMENTÁRIO PUBLICADO NO LINKEDIN COM SUCESSO!*\n\n"
                f"👤 *Autor:* {target_nome}\n"
                f"🔗 *Post:* {post_url}\n\n"
                f"💬 *Seu comentário:*\n\"{comentario}\""
            )
            ev.send_message(whatsapp_recipient, confirm_msg)
        except Exception as e:
            logger.warning("Could not send WhatsApp publication confirmation: %s", e)

    return {
        "status": "PUBLISHED",
        "comment_id": comment_id,
        "target": target_nome,
        "post_url": post_url,
        "details": pub_res,
    }
