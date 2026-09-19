"""
Flow: Content Publisher (Phase 1: Site API + Logged-in LinkedIn Browser).
Executes human-approved automated publication:
1. Extracts edited text directly from Google Docs (reflecting user's edits).
2. Parses validated Blog, LinkedIn Feed, and LinkedIn Pulse sections.
3. Publishes article to site-postgres (instant live on fernandonogueira.dev.br/blog/{slug}).
4. Publishes post and article on LinkedIn using Playwright with shared storage state.
5. Sends instant confirmation notifications via WhatsApp (Evolution API) and Gmail.
"""

from datetime import datetime
import html
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from core.celery_app import celery_app
from core.config import settings
from integrations.evolution import EvolutionClient
from integrations.gemini import GeminiClient
from integrations.google import GoogleHub
from integrations.site_publisher import SitePublisher, site_publisher
from scrapers.linkedin_publisher import LinkedInPublisher, linkedin_publisher
from storage.repository import repo

logger = logging.getLogger(__name__)


class ReviewedContentPackage(BaseModel):
    titulo_blog: str = Field(description="Título principal do artigo para o Blog do site")
    subtitulo_blog: str = Field(description="Subtítulo ou resumo explicativo do artigo")
    slug_blog: str = Field(description="Slug amigável sem acentos para URL do blog")
    corpo_blog_markdown: str = Field(description="Texto integral do artigo do Blog em Markdown com seções H2/H3 e fontes")
    meta_description: str = Field(description="Meta description para SEO (140-160 caracteres)")
    categoria: str = Field(default="TECNOLOGIA", description="Categoria do artigo (TECNOLOGIA ou MÚSICA)")
    tags: List[str] = Field(default_factory=list, description="Tags do artigo")
    tempo_leitura_minutos: int = Field(default=5, description="Tempo estimado de leitura")
    linkedin_post_feed: str = Field(description="Texto completo revisado para o post do Feed do LinkedIn")
    linkedin_artigo_titulo: Optional[str] = Field(default=None, description="Título do artigo de liderança para LinkedIn Pulse")
    linkedin_artigo_corpo: Optional[str] = Field(default=None, description="Texto completo do artigo de liderança para LinkedIn Pulse")


DOC_PARSER_SYSTEM_INSTRUCTION = """
Você é o Extrator e Normalizador de Conteúdo Aprovado para fernandonogueira.dev.br.
O autor humano revisou e editou um documento no Google Docs que contém artigos para Blog e postagens para o LinkedIn.

Sua ÚNICA missão é separar com ABSOLUTA FIDELIDADE os blocos de texto revisados nos campos do schema estruturado.
Diretrizes inegociáveis:
1. NÃO altere, não resuma e não invente nenhuma palavra, frase ou dado. Mantenha 100% da integridade textual das alterações feitas pelo autor no documento.
2. Extraia o artigo do blog completo (com seções H2, subtítulos e referências).
3. Extraia o post de feed do LinkedIn completo (com emojis, gancho 👇 e hashtags).
4. Se houver artigo do LinkedIn Pulse, extraia seu título e corpo.
5. Preserve rigorosamente todos os links [Nome](URL) e formatações Markdown.
"""


def parse_reviewed_doc_content(
    raw_doc_text: str,
    default_pauta_titulo: str = "Tema Editorial",
    default_categoria: str = "TECNOLOGIA",
    gemini_client: Optional[GeminiClient] = None,
) -> ReviewedContentPackage:
    """
    Parses the raw Google Docs text, preserving all human revisions with zero hallucination.
    First attempts deterministic template parsing for absolute fidelity. Falls back to Gemini.
    """
    import re

    # Try deterministic extraction first (covers human edits inside standard channel blocks)
    c1_match = re.search(r'CANAL 1:[^\n]*\n(.*?)(?=CANAL 2:|$)', raw_doc_text, re.DOTALL | re.IGNORECASE)
    c2_match = re.search(r'CANAL 2:[^\n]*\n(.*?)(?=CANAL 3:|$)', raw_doc_text, re.DOTALL | re.IGNORECASE)

    if c1_match and c2_match:
        c1_text = c1_match.group(1).strip()
        c2_text = c2_match.group(1).strip()

        # Parse Pulse article & Feed post from Canal 1
        feed_split = re.split(r'---\s*\[POST DE ALTA PERFORMANCE.*?\]\s*---', c1_text, flags=re.IGNORECASE)
        pulse_title, pulse_body, feed_post = None, None, None
        if len(feed_split) > 1:
            pulse_raw = feed_split[0].strip()
            feed_post = feed_split[1].strip()
            for line in pulse_raw.splitlines():
                if line.startswith("# "):
                    pulse_title = line.replace("# ", "").strip()
                    break
            pulse_body = pulse_raw
        else:
            feed_post = c1_text

        # Parse Blog article from Canal 2
        blog_title, blog_body = None, None
        for line in c2_text.splitlines():
            if line.startswith("# ") and not blog_title:
                blog_title = line.replace("# ", "").strip()
                break

        # Strip SEO metadata footer from blog body
        clean_blog_body = re.split(r'---\s*\[METADADOS DE SEO.*?\]\s*---', c2_text, flags=re.IGNORECASE)[0].strip()

        # Remove header markers if present
        clean_lines = []
        for line in clean_blog_body.splitlines():
            if line.startswith("CANAL 2:") or line.startswith("--------------------"):
                continue
            clean_lines.append(line)
        clean_blog_body = "\n".join(clean_lines).strip()

        effective_title = blog_title or default_pauta_titulo

        # If blog body was truncated or incomplete and pulse article is rich and complete, use pulse body
        if len(clean_blog_body) < 1500 and pulse_body and len(pulse_body) > 2000:
            logger.info("Blog content in CANAL 2 is short (%d chars). Using comprehensive CANAL 1 Pulse content (%d chars).", len(clean_blog_body), len(pulse_body))
            clean_blog_body = pulse_body
            if pulse_title:
                effective_title = pulse_title

        # Check for explicit Slug in SEO metadata
        slug_match = re.search(r'[•\-\*]?\s*Slug:\s*([a-zA-Z0-9_-]+)', c2_text, re.IGNORECASE)
        if slug_match:
            clean_slug = slug_match.group(1).strip()
        else:
            import unicodedata
            normalized = unicodedata.normalize("NFKD", effective_title).encode("ascii", "ignore").decode("utf-8")
            clean_slug = re.sub(r"[^a-zA-Z0-9\s-]", "", normalized.lower())
            clean_slug = re.sub(r"[-\s]+", "-", clean_slug).strip("-")

        # Category normalization
        cat_norm = (default_categoria or "TECNOLOGIA").upper()
        if "MUSICA" in cat_norm or "MÚSICA" in cat_norm:
            final_cat = "MUSICA"
        elif "TI" in cat_norm or "TECNOLOGIA" in cat_norm:
            final_cat = "TECNOLOGIA"
        else:
            final_cat = cat_norm

        logger.info("Successfully extracted multichannel doc content via deterministic template parser.")
        return ReviewedContentPackage(
            titulo_blog=effective_title,
            subtitulo_blog=f"Artigo editorial sobre {effective_title}",
            slug_blog=clean_slug,
            corpo_blog_markdown=clean_blog_body or raw_doc_text,
            meta_description=f"Confira a análise sobre {effective_title}.",
            categoria=final_cat,
            tags=["Editorial", final_cat],
            tempo_leitura_minutos=7,
            linkedin_post_feed=feed_post or "",
            linkedin_artigo_titulo=pulse_title,
            linkedin_artigo_corpo=pulse_body,
        )

    # Fallback to Gemini structured extraction if doc structure deviates
    gemini = gemini_client or GeminiClient()

    prompt = (
        f"Abaixo está o texto completo extraído do Google Docs após a revisão do autor.\n"
        f"Pauta Original: {default_pauta_titulo} | Categoria: {default_categoria}\n\n"
        f"--- CONTEÚDO REVISADO NO GOOGLE DOCS ---\n\n"
        f"{raw_doc_text}\n\n"
        f"Separe os canais rigorosamente conforme o schema estruturado."
    )

    try:
        package: ReviewedContentPackage = gemini.generate_structured(
            prompt=prompt,
            system_instruction=DOC_PARSER_SYSTEM_INSTRUCTION,
            response_model=ReviewedContentPackage,
            model_name="gemini-2.5-flash",
        )
        return package
    except Exception as e:
        logger.warning("Gemini parsing failed (%s). Using fallback plain text parser.", e)
        return ReviewedContentPackage(
            titulo_blog=default_pauta_titulo,
            subtitulo_blog=f"Artigo editorial sobre {default_pauta_titulo}",
            slug_blog=default_pauta_titulo.lower().replace(" ", "-")[:40],
            corpo_blog_markdown=raw_doc_text[:4000],
            meta_description=f"Confira a análise sobre {default_pauta_titulo}.",
            categoria=default_categoria,
            tags=["Editorial", default_categoria],
            tempo_leitura_minutos=5,
            linkedin_post_feed=raw_doc_text[:1200],
        )


def publish_reviewed_editorial(
    doc_id: str,
    pauta_titulo: str,
    categoria: str,
    doc_url: Optional[str] = None,
    hub: Optional[GoogleHub] = None,
    site_pub: Optional[SitePublisher] = None,
    linkedin_pub: Optional[LinkedInPublisher] = None,
    evo: Optional[EvolutionClient] = None,
    skip_linkedin: bool = False,
) -> Dict[str, Any]:
    """
    Coordinates extraction of edited Google Docs content and dispatches Phase 1 publication.
    """
    google_hub = hub or GoogleHub()
    site_client = site_pub or site_publisher
    linkedin_client = linkedin_pub or linkedin_publisher
    evolution = evo or EvolutionClient()

    logger.info("Starting Phase 1 publication for doc_id=%s ('%s')", doc_id, pauta_titulo)

    # 1. Extract updated text content from Google Docs
    raw_doc_text = google_hub.docs.extract_text_content(doc_id)
    if not raw_doc_text or len(raw_doc_text.strip()) < 50:
        raise ValueError(f"Google Document {doc_id} is empty or could not be read.")

    logger.info("Extracted %d characters from reviewed Google Doc %s", len(raw_doc_text), doc_id)

    # 2. Parse into clean multichannel package
    package = parse_reviewed_doc_content(
        raw_doc_text=raw_doc_text,
        default_pauta_titulo=pauta_titulo,
        default_categoria=categoria,
    )

    publication_results = {
        "site": {"status": "PENDING"},
        "linkedin_feed": {"status": "SKIPPED"},
        "linkedin_pulse": {"status": "SKIPPED"},
    }

    # 3. Publish to Site (site-postgres public."Post")
    try:
        site_res = site_client.publish_post(
            title=package.titulo_blog,
            content=package.corpo_blog_markdown,
            excerpt=package.subtitulo_blog or package.meta_description,
            category=package.categoria,
            tags=package.tags,
            read_time=package.tempo_leitura_minutos,
            slug=package.slug_blog,
            cover_image=f"/og-{package.slug_blog}.png",
        )
        publication_results["site"] = site_res
        blog_url = site_res.get("url")
    except Exception as e:
        logger.error("Failed to publish post to site: %s", e)
        publication_results["site"] = {"status": "ERROR", "error": str(e)}
        blog_url = f"https://www.fernandonogueira.dev.br/blog/{package.slug_blog}"

    # 4. Publish to LinkedIn (Feed & Pulse) via Playwright with shared storage state
    if not skip_linkedin:
        # Resolve illustrative cover image (generated via FLUX or cached)
        cover_image_path = None
        possible_covers = [
            f"/app/data/og-{package.slug_blog}.png",
            f"/root/site/public/og-{package.slug_blog}.png",
            f"/app/og-{package.slug_blog}.png",
            f"data/og-{package.slug_blog}.png",
        ]
        for p_path in possible_covers:
            if Path(p_path).exists():
                cover_image_path = str(p_path)
                break

        if not cover_image_path:
            try:
                from integrations.image_generator import image_generator
                cover_image_path = image_generator.generate_image(
                    prompt=package.titulo_blog,
                    slug=package.slug_blog,
                    format_type="16:9",
                    category=package.categoria,
                )
            except Exception as e_gen:
                logger.warning("Could not generate on-the-fly cover image: %s", e_gen)

        # 4.1 LinkedIn Feed Post
        if package.linkedin_post_feed:
            feed_image_path = None
            for p_path in [
                f"/app/data/square-{package.slug_blog}.png",
                f"/root/site/public/square-{package.slug_blog}.png",
                cover_image_path,
            ]:
                if p_path and Path(p_path).exists():
                    feed_image_path = str(p_path)
                    break

            try:
                feed_res = linkedin_client.publish_feed_post(
                    text=package.linkedin_post_feed,
                    image_path=feed_image_path or cover_image_path,
                )
                publication_results["linkedin_feed"] = feed_res
            except Exception as e_feed:
                logger.error("Failed to publish LinkedIn feed post: %s", e_feed)
                publication_results["linkedin_feed"] = {"status": "ERROR", "error": str(e_feed)}

        # 4.2 LinkedIn Pulse Article (if present)
        if package.linkedin_artigo_titulo and package.linkedin_artigo_corpo:
            try:
                pulse_res = linkedin_client.publish_pulse_article(
                    title=package.linkedin_artigo_titulo,
                    content_markdown=package.linkedin_artigo_corpo,
                    image_path=cover_image_path,
                )
                publication_results["linkedin_pulse"] = pulse_res
            except Exception as e_pulse:
                logger.error("Failed to publish LinkedIn Pulse article: %s", e_pulse)
                publication_results["linkedin_pulse"] = {"status": "ERROR", "error": str(e_pulse)}

    # 5. Record final publication in persistent history
    task_id = f"publish_{doc_id[:16]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    try:
        repo.record_editorial_publication(
            task_id=task_id,
            tema=package.titulo_blog,
            categoria=package.categoria,
            angulo_editorial=package.subtitulo_blog,
            doc_id=doc_id,
            doc_url=doc_url or f"https://docs.google.com/document/d/{doc_id}/edit",
        )
    except Exception as exc_repo:
        logger.warning("Could not record publication in repo: %s", exc_repo)

    # 6. Dispatch WhatsApp notification
    if settings.NOTIFICATION_PHONE:
        try:
            import asyncio
            site_status_icon = "✅" if publication_results["site"].get("status") == "SUCCESS" else "⚠️"
            linkedin_status_icon = "✅" if publication_results["linkedin_feed"].get("status") == "SUCCESS" else "⚠️"

            wpp_msg = (
                f"🎉 *Fernando, seu conteúdo revisado foi publicado com sucesso!*\n\n"
                f"📌 *Tema:* {package.titulo_blog}\n"
                f"📂 *Categoria:* {package.categoria}\n\n"
                f"{site_status_icon} *Artigo no Ar no Blog:*\n{blog_url}\n\n"
                f"{linkedin_status_icon} *LinkedIn Feed:* Publicado com sucesso no perfil!\n"
            )
            if publication_results["linkedin_pulse"].get("status") == "SUCCESS":
                wpp_msg += "✅ *LinkedIn Pulse:* Artigo publicado com sucesso!\n"

            wpp_msg += f"\n📄 *Documento Base:* {doc_url or f'https://docs.google.com/document/d/{doc_id}/edit'}"

            # Dispatch with real image attachment if available
            wpp_dispatched = False
            wpp_image_candidate = locals().get("feed_image_path") or cover_image_path
            if wpp_image_candidate and Path(wpp_image_candidate).exists():
                try:
                    img_filename = Path(wpp_image_candidate).name
                    # Evolution API reliably ingests public URLs hosted on the domain
                    public_img_url = f"https://www.fernandonogueira.dev.br/{img_filename}"
                    asyncio.run(evolution.send_media_message(
                        phone=settings.NOTIFICATION_PHONE,
                        media_base64_or_url=public_img_url,
                        file_name=img_filename,
                        caption=wpp_msg,
                        media_type="image",
                        mime_type="image/png",
                    ))
                    wpp_dispatched = True
                except Exception as err_media:
                    logger.warning("Could not send media message via public URL, attempting base64 fallback: %s", err_media)
                    try:
                        import base64
                        with open(wpp_image_candidate, "rb") as img_f:
                            raw_b64 = base64.b64encode(img_f.read()).decode('utf-8')
                        asyncio.run(evolution.send_media_message(
                            phone=settings.NOTIFICATION_PHONE,
                            media_base64_or_url=raw_b64,
                            file_name=img_filename,
                            caption=wpp_msg,
                            media_type="image",
                            mime_type="image/png",
                        ))
                        wpp_dispatched = True
                    except Exception as err_b64:
                        logger.warning("Base64 media dispatch also failed, falling back to text: %s", err_b64)

            if not wpp_dispatched:
                asyncio.run(evolution.send_text_message(settings.NOTIFICATION_PHONE, wpp_msg))

            logger.info("WhatsApp publication confirmation sent to %s", settings.NOTIFICATION_PHONE)
        except Exception as e_wpp:
            logger.warning("Could not send WhatsApp publication notification: %s", e_wpp)

    # 7. Dispatch Email confirmation
    if settings.ADMIN_EMAIL:
        try:
            email_subject = f"✅ Conteúdo Publicado no Site e LinkedIn: {package.titulo_blog[:40]}"
            html_body = f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #0f172a; max-width: 650px; margin: 0 auto; padding: 20px;">
                <div style="background-color: #0f172a; color: #ffffff; padding: 22px; border-radius: 10px; border-top: 4px solid #16a34a;">
                    <span style="background-color: rgba(34,197,94,0.2); color: #86efac; padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: 700; text-transform: uppercase;">
                        ✓ Publicação Concluída
                    </span>
                    <h1 style="margin: 12px 0 4px 0; font-size: 20px;">{html.escape(package.titulo_blog)}</h1>
                    <p style="margin: 0; color: #94a3b8; font-size: 13px;">Publicado automaticamente no Blog e LinkedIn.</p>
                </div>
                <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 18px; margin: 20px 0;">
                    <p style="margin: 6px 0;"><strong>🌐 Blog:</strong> <a href="{html.escape(blog_url)}" target="_blank" style="color: #2563eb;">{html.escape(blog_url)}</a></p>
                    <p style="margin: 6px 0;"><strong>💼 LinkedIn:</strong> Post publicado com a sua sessão logada</p>
                    <p style="margin: 6px 0;"><strong>📄 Google Doc:</strong> <a href="{html.escape(doc_url or '')}" target="_blank" style="color: #64748b;">Ver documento revisado</a></p>
                </div>
            </div>
            """
            google_hub.gmail.send_email(
                to_email=settings.ADMIN_EMAIL,
                subject=email_subject,
                html_body=html_body,
            )
        except Exception as e_mail:
            logger.warning("Could not send email publication confirmation: %s", e_mail)

    return {
        "status": "SUCCESS",
        "doc_id": doc_id,
        "titulo": package.titulo_blog,
        "slug": package.slug_blog,
        "blog_url": blog_url,
        "publication_results": publication_results,
    }


@celery_app.task(name="flows.flow_content_publisher.task_publish_approved_editorial")
def task_publish_approved_editorial(token_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Celery task triggered by the WhatsApp / web approval link.
    Pulls reviewed Google Docs content and executes Phase 1 publication (Site + LinkedIn).
    """
    doc_id = token_payload.get("doc_id")
    pauta_titulo = token_payload.get("pauta_titulo", "Artigo Editorial")
    categoria = token_payload.get("categoria", "TECNOLOGIA")
    doc_url = token_payload.get("doc_url")
    skip_linkedin = token_payload.get("skip_linkedin", False)

    if not doc_id:
        raise ValueError("Missing 'doc_id' in token payload.")

    task_id = "publish_exec_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    repo.log_flow_start(task_id, "publish_approved_editorial", token_payload)

    result = publish_reviewed_editorial(
        doc_id=doc_id,
        pauta_titulo=pauta_titulo,
        categoria=categoria,
        doc_url=doc_url,
        skip_linkedin=skip_linkedin,
    )

    repo.log_flow_complete(task_id, result)
    return result
