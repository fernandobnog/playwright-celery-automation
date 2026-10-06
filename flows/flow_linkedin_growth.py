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
            "Comentário ultra-conciso (1 a 3 frases no total, no máximo 35 a 50 palavras), "
            "altamente pessoal, humano, em 1ª pessoa, direto ao ponto e sem clichês ou rodeios de IA."
        )
    )
    tese_central: str = Field(description="Resumo em 1 frase curta da tese do post analisado")
    angulo_utilizado: str = Field(description="Ângulo explorado: vivência prática, contraponto pontual ou pergunta rápida")


SNIPER_COMMENT_SYSTEM_INSTRUCTION = """
Você é Fernando Nogueira, Arquiteto de Soluções e Engenheiro de Software (fernandonogueira.dev.br).
Você está comentando em um post no LinkedIn de um colega ou líder profissional (TI, Jurídico/LegalOps ou RH).

DIRETRIZES DE ESTILO E VOZ:
1. EXTENSÃO ULTRA-CURTA: Exatamente 1 a 3 frases curtas (máximo 40 a 55 palavras no total). NUNCA escreva parágrafos longos, pareceres acadêmicos ou relatórios de auditoria.
2. TOM PESSOAL E HUMANO (1ª PESSOA): Fale como um colega de trincheira trocando ideia sincera no dia a dia. Use expressões naturais como:
   - "Ponto cirúrgico, [Primeiro Nome]."
   - "Aqui na prática vejo muito isso acontecer..."
   - "Concordo, e o gargalo que mais sinto no dia a dia é..."
   - "Excelente reflexão. Na bancada a gente percebe que..."
3. MENOS TÉCNICO-ABSTRATO, MAIS PRÁTICO:
   - Evite jargões frios como "accountability por design", "auditoria de prompts", "rastreabilidade algorítmica estrita".
   - Prefira falar da realidade prática: a dificuldade de alinhar o time, a pressa de colocar IA sem arrumar os processos antes, a importância de testar antes de colocar em produção.
4. ZERO CLICHÊS DE IA:
   - Expressamente proibido usar: "no cenário atual", "no mundo de hoje", "é fundamental", "divisor de águas", "mergulhar fundo", "um verdadeiro farol".
   - Comece direto no ponto. Nunca use introduções burocráticas ("Li com atenção sua publicação...").
5. FECHAMENTO SIMPLES:
   - Termine com uma pergunta curta e natural que estimule uma conversa leve (ex: "Vocês também sentiram esse impacto por aí?", "Como tem sido a adesão do time no dia a dia?").
"""


def generate_sniper_comment(
    post_text: str,
    author_name: str,
    nicho: str,
    gemini_client: Optional[GeminiClient] = None,
) -> SniperCommentResult:
    """
    Synthesizes a short, punchy, human sniper comment using Gemini and Few-Shot style memory.
    """
    gemini = gemini_client or GeminiClient()

    few_shot_section = ""
    try:
        examples = repo.get_style_examples(limit=2, nicho=nicho)
        if examples:
            shots = []
            for ex in examples:
                shots.append(
                    f"- Post: \"{ex['post_texto'][:140]}...\"\n"
                    f"  Comentário real de Fernando: \"{ex['comentario_final']}\""
                )
            few_shot_section = "\n\n--- EXEMPLOS REAIS DO ESTILO DE ESCRITA DE FERNANDO NOGUEIRA ---\n" + "\n".join(shots) + "\n---------------------------------------------------------------\n"
    except Exception as e:
        logger.debug("Could not fetch style examples: %s", e)

    cognee_section = ""
    try:
        from integrations.cognee_service import cognee_service
        cognee_section = cognee_service.retrieve_style_context(post_text=post_text, nicho=nicho)
    except Exception as e:
        logger.debug("Could not fetch Cognee style context: %s", e)

    author_first_name = author_name.split()[0] if author_name else ""

    prompt = (
        f"Analise a publicação recente no LinkedIn do autor '{author_name}' (Primeiro nome: {author_first_name}, Nicho: {nicho}):\n\n"
        f"--- CONTEÚDO DO POST ---\n"
        f"{post_text[:2500]}\n"
        f"-------------------------\n"
        f"{few_shot_section}\n"
        f"{cognee_section}\n"
        f"Redija um comentário curto, descontraído, pessoal (em 1ª pessoa) e cirúrgico (1 a 3 frases, máximo 50 palavras). "
        f"Conecte diretamente com {author_first_name or 'o autor'} e termine com uma pergunta curta e envolvente."
    )

    result: SniperCommentResult = gemini.generate_structured(
        prompt=prompt,
        system_instruction=SNIPER_COMMENT_SYSTEM_INSTRUCTION,
        response_model=SniperCommentResult,
        model_name="gemini-2.5-flash",
    )
    return result


def refine_sniper_comment(
    post_text: str,
    current_comment: str,
    instruction: str,
    author_name: str = "",
    nicho: str = "GERAL",
    gemini_client: Optional[GeminiClient] = None,
) -> SniperCommentResult:
    """
    Refines an existing sniper comment based on Fernando's specific feedback or instruction.
    """
    gemini = gemini_client or GeminiClient()
    author_first_name = author_name.split()[0] if author_name else "autor"

    cognee_section = ""
    try:
        from integrations.cognee_service import cognee_service
        cognee_section = cognee_service.retrieve_style_context(post_text=post_text, nicho=nicho)
    except Exception as e:
        logger.debug("Could not fetch Cognee style context: %s", e)

    prompt = (
        f"Contexto do post original de {author_name or 'LinkedIn'} ({nicho}):\n"
        f"{post_text[:1500]}\n\n"
        f"Comentário sugerido anteriormente:\n\"{current_comment}\"\n\n"
        f"INSTRUÇÃO DE AJUSTE DADA POR FERNANDO NOGUEIRA:\n"
        f"\"{instruction}\"\n\n"
        f"{cognee_section}\n"
        f"Reescreva o comentário atendendo rigorosamente à instrução acima, "
        f"mantendo a voz pessoal, concisa (1 a 3 frases, máximo 50 palavras) e autêntica de Fernando Nogueira."
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
    whatsapp_recipient = getattr(settings, "EDITORIAL_WHATSAPP_RECIPIENT", None) or getattr(settings, "NOTIFICATION_PHONE", None)

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

            # Format approval & like URLs
            approve_url = f"https://fernandonogueira.dev.br/api/v1/editorial/linkedin-comment/approve?token={action_token}"
            like_url = f"https://fernandonogueira.dev.br/api/v1/editorial/linkedin-comment/like?token={action_token}"

            # Dispatch notification to WhatsApp
            snippet = post_text[:280].replace("\n", " ").strip()
            wa_text = (
                f"🎯 *SNIPER LINKEDIN: NOVO POST DETECTADO*\n\n"
                f"👤 *Autor:* {target_nome} [{nicho}]\n"
                f"⏱️ *Publicado há:* {time_text or 'recente'}\n"
                f"🔗 *Post Original:* {post_url}\n\n"
                f"📝 *Trecho do Post:*\n\"{snippet}...\"\n\n"
                f"💡 *Sugestão de Comentário (Pessoal & Conciso):*\n\"{comment_res.comentario}\"\n\n"
                f"👉 *Para aprovar comentário:* {approve_url}\n\n"
                f"👍 *Para apenas CURTIR o post:* {like_url}\n\n"
                f"⚙️ *Para editar ou refinar com IA:* https://fernandonogueira.dev.br/admin/linkedin"
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

    # Record into style memory for continuous few-shot learning
    try:
        repo.record_style_memory(comment_id=comment_id, final_comment=comentario)
    except Exception as e:
        logger.warning("Could not record style memory for comment #%d: %s", comment_id, e)

    whatsapp_recipient = getattr(settings, "EDITORIAL_WHATSAPP_RECIPIENT", None) or getattr(settings, "NOTIFICATION_PHONE", None)
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


@celery_app.task(
    name="flows.flow_linkedin_growth.task_like_approved_linkedin_post",
    queue="scraping",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def task_like_approved_linkedin_post(self, comment_id: int) -> Dict[str, Any]:
    """
    Executes like reaction on the target's LinkedIn post via Playwright.
    """
    logger.info("Executing like reaction on LinkedIn post for comment record #%d...", comment_id)
    comment = repo.get_linkedin_growth_comment_by_id(comment_id)
    if not comment:
        raise ValueError(f"Comment record #{comment_id} not found in database.")

    if comment["status"] in ("LIKED", "PUBLISHED"):
        logger.info("Comment record #%d already has status %s. Skipping like.", comment_id, comment.get("status"))
        return {"status": "ALREADY_PROCESSED", "comment_id": comment_id}

    post_url = comment["post_url"]
    target_nome = comment["target_nome"]

    # Execute like reaction
    like_res = linkedin_publisher.like_post(post_url=post_url)

    # Mark as LIKED in repository
    repo.update_linkedin_growth_comment_status(comment_id=comment_id, status="LIKED")

    whatsapp_recipient = getattr(settings, "EDITORIAL_WHATSAPP_RECIPIENT", None) or getattr(settings, "NOTIFICATION_PHONE", None)
    if whatsapp_recipient:
        try:
            ev = EvolutionClient()
            confirm_msg = (
                f"👍 *POST CURTIDO NO LINKEDIN COM SUCESSO!*\n\n"
                f"👤 *Autor:* {target_nome}\n"
                f"🔗 *Post:* {post_url}\n"
                f"⚡ Publicação marcada como curtida pelo Playwright."
            )
            ev.send_message(whatsapp_recipient, confirm_msg)
        except Exception as e:
            logger.warning("Could not send WhatsApp like confirmation: %s", e)

    return {
        "status": "LIKED",
        "comment_id": comment_id,
        "target": target_nome,
        "post_url": post_url,
        "details": like_res,
    }

