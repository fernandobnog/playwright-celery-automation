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
            "Comentário ultra-conciso (1 a 3 frases no total, no máximo 30 a 45 palavras), "
            "altamente pessoal, humano, em 1ª pessoa, direto ao ponto. "
            "JAMAIS comece com elogios clichês (ponto cirúrgico, excelente...), "
            "NUNCA cite nomes de pessoas ou empresas, "
            "NUNCA use o termo 'bancada' e "
            "NUNCA termine com perguntas (sem ponto de interrogação)."
        )
    )
    tese_central: str = Field(description="Resumo em 1 frase curta da tese do post analisado")
    angulo_utilizado: str = Field(description="Ângulo explorado: vivência prática, contraponto pontual ou visão executiva")


SNIPER_COMMENT_SYSTEM_INSTRUCTION = """
Você é Fernando Nogueira, Arquiteto de Soluções, CTO da NTAPP e Especialista em IA para o setor jurídico (fernandonogueira.dev.br).
Você está comentando em um post no LinkedIn de um profissional do ecossistema de TI, Jurídico/LegalOps ou RH.

DIRETRIZES FUNDAMENTAIS DE VOZ E ESTILO:
1. EXTENSÃO ULTRA-CURTA: Exatamente 1 a 3 frases curtas (máximo 30 a 45 palavras no total). NUNCA escreva parágrafos longos, pareceres acadêmicos ou relatórios.
2. TOM PESSOAL E HUMANO (1ª PESSOA): Fale como um par técnico e executivo sênior compartilhando vivência real de trincheira ("vejo", "sinto", "a gente nota", "por aqui").
3. VÁ DIRETO AO PONTO (IN MEDIA RES):
   - Comece direto pelo insight, constatação prática ou argumento central.
   - NUNCA use cumprimentos, bajulações ou introduções mornas.

PROIBIÇÕES ABSOLUTAS (TOLERÂNCIA ZERO):
1. PROIBIDO ABERTURAS CLICHÊS E ELOGIOS MECÂNICOS:
   - JAMAIS comece com: "Ponto cirúrgico", "Excelente iniciativa", "Excelente visão", "Excelente leitura", "Perfeito", "Visão muito necessária", "Concordo 100%", "Provocação pertinente", "Parabéns", "Muito bom", "Grande movimento".
   - Comece diretamente com o fato, o impacto ou o contraponto prático.
2. PROIBIDO CITAR NOMES DE PESSOAS OU EMPRESAS (SEM VOCATIVO):
   - JAMAIS se dirija à pessoa pelo nome ("Silvio,", "Alexandre,", "Paulo,") e JAMAIS cite nomes de empresas ou entidades (ex: "ACC", "Cia de Talentos", "NTAPP", etc.).
   - Trate o assunto de forma impessoal e direta, focando na ideia e na realidade técnica, não no indivíduo ou na marca.
3. PROIBIDO USAR O TERMO "BANCADA":
   - JAMAIS use a palavra "bancada" ("na bancada", "aqui na bancada"). Esse termo não é usado normalmente nesse contexto.
   - Use termos naturais: "no dia a dia", "na prática de produção", "na rotina dos projetos", "por aqui", "quando rodamos isso na ponta".
4. PROIBIDO TERMINAR COM PERGUNTAS (SEM INTERROGAÇÕES):
   - NUNCA termine com uma pergunta. Não use ponto de interrogação ("?") no comentário.
   - Não pergunte "como tem sido...", "o que acham...", "será que...", "concordam?".
   - O encerramento DEVE ser uma afirmação assertiva, uma constatação realista ou um fechamento sólido de quem vivencia a prática.
5. PROIBIDO CLICHÊS DE IA:
   - NUNCA use jargões como: "no cenário atual", "no mundo de hoje", "é fundamental", "divisor de águas", "mergulhar fundo", "um verdadeiro farol", "virada de chave", "navegar por águas".

EXEMPLOS DO PADRÃO CORRETO DE ESCRITA:
- "Quando colocamos LLMs em produção para fluxos complexos, o maior gargalo técnico quase nunca é o modelo, mas sim o saneamento da base legada. Quem tenta queimar essa etapa acaba pagando a conta dobrada no suporte."
- "Na prática, a governança de dados só funciona quando está embutida no pipeline de engenharia, sem criar camadas manuais de aprovação. Se o processo engessa a ponta, os times acabam contornando a regra."
- "O desafio da automação em operações sensíveis não é a geração do texto, mas sim a rastreabilidade das decisões tomadas pelos agentes. Sem trilha clara de auditoria, o ganho de velocidade vira passivo operacional."
"""


import re

def sanitize_sniper_comment(text: str) -> str:
    """
    Cleans sniper comment according to Fernando's strict voice guidelines:
    - Strips clichéd praise openings ('Ponto cirúrgico', 'Excelente...', etc.)
    - Strips leading vocatives / person names ('Alexandre, ', 'Silvio, ', etc.)
    - Replaces accidental occurrences of 'bancada'
    - Strips any ending questions (removes '?' and trailing question sentences)
    """
    text = (text or "").strip()
    if not text:
        return ""

    # 1. Remove clichéd praise openers and leading vocatives
    cliches = [
        r'^(ponto cir[uú]rgico|excelente (iniciativa|vis[aã]o|leitura|post|an[aá]lise)|vis[aã]o (muito )?necess[aá]ria|concordo 100%|perfeito|muito bom|parab[eé]ns( pelos? [^,.:!]+)?|grande iniciativa|provoca[cç][aã]o muito pertinente)[,.:! ]*',
        r'^[A-ZÁÉÍÓÚÂÊÔÃÕ][a-záéíóúâêôãõ]+[,.:! ]+',
    ]
    for _ in range(2):
        for pattern in cliches:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE).strip()

    # 2. Ban 'bancada'
    text = re.sub(r'\b(aqui na bancada|na bancada)\b', 'no dia a dia', text, flags=re.IGNORECASE)
    text = re.sub(r'\bbancada\b', 'prática', text, flags=re.IGNORECASE)

    # 3. Remove ending questions
    if '?' in text:
        # Split sentences by punctuation (. ! ?)
        sentences = re.split(r'(?<=[.!?])\s+', text)
        non_questions = [s for s in sentences if not s.strip().endswith('?')]
        if non_questions:
            text = ' '.join(non_questions).strip()
        else:
            # If the entire comment was a question, convert ? to . and remove leading interrogative words
            text = text.rstrip('?').strip() + '.'
            text = re.sub(r'^(ser[aá] que|como|ser[aá]|qual|por que)\s+', '', text, flags=re.IGNORECASE)

    # Clean up double spaces or residual punctuation at start
    text = re.sub(r'^[,.:;!\s]+', '', text).strip()
    if text:
        text = text[0].upper() + text[1:]
    return text.strip()


def generate_sniper_comment(
    post_text: str,
    author_name: str,
    nicho: str,
    target_name: Optional[str] = None,
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

    prompt = (
        f"Analise a publicação recente no LinkedIn (Nicho: {nicho}):\n\n"
        f"--- CONTEÚDO DO POST ---\n"
        f"{post_text[:2500]}\n"
        f"-------------------------\n"
        f"{few_shot_section}\n"
        f"{cognee_section}\n"
        f"Redija um comentário autêntico de Fernando Nogueira, em 1ª pessoa, conciso (1 a 3 frases, máximo 40 palavras) e de alto valor prático.\n"
        f"REGRAS OBRIGATÓRIAS:\n"
        f"1. Vá DIRETO ao insight sem introduções clichês ('ponto cirúrgico', 'excelente iniciativa', etc.).\n"
        f"2. NÃO cite o nome de pessoas nem de empresas.\n"
        f"3. NUNCA use a palavra 'bancada'.\n"
        f"4. NUNCA termine com perguntas. Finalize com uma constatação assertiva e contundente."
    )

    result: SniperCommentResult = gemini.generate_structured(
        prompt=prompt,
        system_instruction=SNIPER_COMMENT_SYSTEM_INSTRUCTION,
        response_model=SniperCommentResult,
        model_name="gemini-2.5-flash",
    )
    result.comentario = sanitize_sniper_comment(result.comentario)
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

    few_shot_section = ""
    try:
        from storage.repository import repo
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
        logger.debug("Could not fetch style examples for refinement: %s", e)

    cognee_section = ""
    try:
        from integrations.cognee_service import cognee_service
        cognee_section = cognee_service.retrieve_style_context(post_text=post_text, nicho=nicho)
    except Exception as e:
        logger.debug("Could not fetch Cognee style context: %s", e)

    prompt = (
        f"Contexto do post original ({nicho}):\n"
        f"{post_text[:1500]}\n\n"
        f"Comentário sugerido anteriormente:\n\"{current_comment}\"\n\n"
        f"INSTRUÇÃO DE AJUSTE DADA POR FERNANDO NOGUEIRA:\n"
        f"\"{instruction}\"\n\n"
        f"{few_shot_section}\n"
        f"{cognee_section}\n"
        f"Reescreva o comentário atendendo rigorosamente à instrução acima, "
        f"mantendo a voz pessoal, concisa (1 a 3 frases, máximo 40 palavras) de Fernando Nogueira.\n"
        f"REGRAS OBRIGATÓRIAS:\n"
        f"- NUNCA use clichês como 'ponto cirúrgico', 'excelente iniciativa', etc.\n"
        f"- NÃO cite o nome de pessoas nem de empresas.\n"
        f"- NUNCA use a palavra 'bancada'.\n"
        f"- NUNCA termine com perguntas."
    )

    result: SniperCommentResult = gemini.generate_structured(
        prompt=prompt,
        system_instruction=SNIPER_COMMENT_SYSTEM_INSTRUCTION,
        response_model=SniperCommentResult,
        model_name="gemini-2.5-flash",
    )
    result.comentario = sanitize_sniper_comment(result.comentario)
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
            latest_post = linkedin_publisher.get_latest_post_from_profile(
                profile_url=profile_url,
                max_age_days=5.0,
                target_name=target_nome,
            )
            # Update check timestamp
            post_id = latest_post.get("post_id") if latest_post else None
            repo.update_target_last_check(target_id, post_id or last_seen_post_id)

            if not latest_post or not latest_post.get("text"):
                logger.info("Nenhum post recente (máximo 5 dias) encontrado para o alvo %s.", target_nome)
                continue

            post_url = latest_post.get("post_url")
            if not post_url or post_url == profile_url:
                logger.warning("Post encontrado para %s não possui URL válida de publicação (%s). Ignorando.", target_nome, post_url)
                continue

            post_text = latest_post["text"]
            post_author = latest_post.get("author") or target_nome
            time_text = latest_post.get("time_text", "")
            age_days = latest_post.get("age_days", 0.0)

            # Check if this post ID or URL was already checked or exists in comments
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

            logger.info("New post detected for %s (age=%.1fd)! Generating sniper comment...", target_nome, age_days)
            comment_res = generate_sniper_comment(
                post_text=post_text,
                author_name=post_author,
                nicho=nicho,
                target_name=target_nome,
                gemini_client=gemini,
            )

            # Save draft in SQLite (full text up to 10,000 characters)
            comment_id = repo.save_linkedin_growth_comment(
                target_id=target_id,
                target_nome=target_nome,
                post_url=post_url,
                post_texto=post_text[:10000],
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
                f"⏱️ *Publicado há:* {time_text or 'recente'} (<= 5 dias)\n"
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

    try:
        # Execute publication with humanized typing
        pub_res = linkedin_publisher.comment_on_post(
            post_url=post_url,
            comment_text=comentario,
        )

        # Also register organic like reaction on the post
        try:
            linkedin_publisher.like_post(post_url=post_url)
            logger.info("Automatically liked post %s alongside comment #%d.", post_url, comment_id)
        except Exception as err_like:
            logger.warning("Could not like post %s alongside comment #%d: %s", post_url, comment_id, err_like)

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
    except Exception as exc:
        logger.error("Failed to publish approved comment #%d: %s", comment_id, exc, exc_info=True)
        if self.request.retries < self.max_retries:
            logger.info("Retrying comment #%d publication (attempt %d/%d)...", comment_id, self.request.retries + 1, self.max_retries)
            raise self.retry(exc=exc)

        # If all retries exhausted, mark comment status as FAILED_PUBLISH
        repo.update_linkedin_growth_comment_status(comment_id=comment_id, status="FAILED_PUBLISH")

        whatsapp_recipient = getattr(settings, "EDITORIAL_WHATSAPP_RECIPIENT", None) or getattr(settings, "NOTIFICATION_PHONE", None)
        if whatsapp_recipient:
            try:
                ev = EvolutionClient()
                err_msg = (
                    f"⚠️ *FALHA AO PUBLICAR COMENTÁRIO NO LINKEDIN*\n\n"
                    f"👤 *Autor:* {target_nome}\n"
                    f"🔗 *Post:* {post_url}\n\n"
                    f"❌ *Erro:* {str(exc)[:200]}\n\n"
                    f"⚙️ Você pode tentar novamente no painel: https://fernandonogueira.dev.br/admin/linkedin"
                )
                ev.send_message(whatsapp_recipient, err_msg)
            except Exception:
                pass
        raise exc


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

    post_url = comment["post_url"]
    target_nome = comment["target_nome"]

    try:
        # Execute like reaction via Playwright
        like_res = linkedin_publisher.like_post(post_url=post_url)

        # Mark as LIKED in repository only upon successful verification
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
    except Exception as exc:
        logger.error("Failed to like LinkedIn post %s for comment #%d: %s", post_url, comment_id, exc)
        repo.update_linkedin_growth_comment_status(comment_id=comment_id, status="FAILED_LIKE")
        try:
            whatsapp_recipient = getattr(settings, "EDITORIAL_WHATSAPP_RECIPIENT", None) or getattr(settings, "NOTIFICATION_PHONE", None)
            if whatsapp_recipient:
                ev = EvolutionClient()
                err_msg = (
                    f"⚠️ *FALHA AO CURTIR POST NO LINKEDIN*\n\n"
                    f"👤 *Autor:* {target_nome}\n"
                    f"🔗 *Post:* {post_url}\n"
                    f"❌ *Erro:* {str(exc)[:250]}"
                )
                ev.send_message(whatsapp_recipient, err_msg)
        except Exception:
            pass
        raise exc

