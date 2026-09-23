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


def strip_editorial_preamble(md_text: str) -> str:
    """
    Strips metadata headers, repeated titles, reading time estimates,
    and cover image suggestions from markdown text intended for blog publication.
    """
    import re
    lines = []
    skip_header = True
    for line in md_text.splitlines():
        trimmed = line.strip()
        if re.match(r'^[=\-_\*]{3,}$', trimmed):
            continue
        if trimmed.startswith("CANAL ") or "ARTIGO COMPLETO" in trimmed:
            continue
        if (
            trimmed.startswith("⏱️")
            or trimmed.startswith("🖼️")
            or trimmed.lower().startswith("sugestão de imagem")
            or trimmed.lower().startswith("tempo de leitura")
        ):
            continue
        if skip_header and (trimmed.startswith("# ") or trimmed.startswith("*") or not trimmed):
            continue
        skip_header = False
        lines.append(line)
    return "\n".join(lines).strip()


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

        # Clean feed_post of trailing divider lines and section headers
        if feed_post:
            clean_feed_lines = []
            for line in feed_post.splitlines():
                trimmed = line.strip()
                if re.match(r'^[=\-_\*]{3,}$', trimmed):
                    continue
                if trimmed.startswith("CANAL "):
                    continue
                clean_feed_lines.append(line)
            feed_post = "\n".join(clean_feed_lines).strip()

        # Clean pulse_body of leading/trailing divider lines and metadata
        if pulse_body:
            clean_pulse_lines = []
            for line in pulse_body.splitlines():
                trimmed = line.strip()
                if re.match(r'^[=\-_\*]{3,}$', trimmed):
                    continue
                if trimmed.startswith("CANAL ") or "ARTIGO COMPLETO" in trimmed:
                    continue
                clean_pulse_lines.append(line)
            pulse_body = "\n".join(clean_pulse_lines).strip()

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

        # Only fallback to CANAL 1 if CANAL 2 is genuinely missing or severely incomplete (< 1000 chars and pulse is significantly longer)
        if len(clean_blog_body) < 1000 and pulse_body and len(pulse_body) > len(clean_blog_body):
            logger.info("Blog content in CANAL 2 is incomplete (%d chars). Using CANAL 1 Pulse content (%d chars).", len(clean_blog_body), len(pulse_body))
            clean_blog_body = pulse_body
            if pulse_title:
                effective_title = pulse_title

        # Strip metadata, repeated titles, reading time, and cover image suggestions
        clean_blog_body = strip_editorial_preamble(clean_blog_body)

        # Extract subtitle if available in doc format (*Subtitle*)
        sub_match = re.search(r'^\*([^\*\n]+)\*', c2_text, re.MULTILINE) or re.search(r'^\*([^\*\n]+)\*', c1_text, re.MULTILINE)
        extracted_subtitle = sub_match.group(1).strip() if sub_match else f"Artigo editorial sobre {effective_title}"

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
            subtitulo_blog=extracted_subtitle,
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
    publish_linkedin_feed: bool = False,
) -> Dict[str, Any]:
    """
    Coordinates extraction of edited Google Docs content and dispatches Phase 1 publication.
    By default, publishes the long-form leadership article on LinkedIn Pulse (omitting redundant feed post).
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

    # 4. Publish to LinkedIn via Playwright with shared storage state (Focus: Pulse Article)
    if not skip_linkedin:
        # Resolve illustrative cover image:
        # Priority 1: Check if user pasted an image directly into the Google Doc during review
        cover_image_path = None
        data_cover = f"/app/data/og-{package.slug_blog}.png"
        site_cover = f"/root/site/public/og-{package.slug_blog}.png"
        target_doc_cover = site_cover if Path("/root/site/public").exists() else data_cover
        try:
            extracted_doc_img = google_hub.docs.download_first_inline_image(
                document_id=doc_id,
                dest_path=target_doc_cover,
            )
            if extracted_doc_img:
                cover_image_path = extracted_doc_img
                # Also ensure image exists in both data and site/public
                for alt_path in [data_cover, site_cover]:
                    if alt_path != extracted_doc_img:
                        try:
                            alt_p = Path(alt_path)
                            alt_p.parent.mkdir(parents=True, exist_ok=True)
                            import shutil
                            shutil.copyfile(extracted_doc_img, str(alt_p))
                        except Exception:
                            pass
                logger.info("Found and downloaded user-provided cover image from Google Doc: %s", cover_image_path)
        except Exception as e_doc_img:
            logger.debug("No inline cover image in Google Doc (%s)", e_doc_img)

        # Priority 2: Pre-existing cover file on disk
        if not cover_image_path:
            possible_covers = [
                f"/root/site/public/og-{package.slug_blog}.png",
                f"/app/data/og-{package.slug_blog}.png",
                f"/app/og-{package.slug_blog}.png",
                f"data/og-{package.slug_blog}.png",
            ]
            for p_path in possible_covers:
                if Path(p_path).exists():
                    cover_image_path = str(p_path)
                    break

        # Priority 3: Fallback on-the-fly generation if none provided
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

        # 4.1 LinkedIn Feed Post (Disabled by default: publication focused exclusively on Pulse Article)
        if publish_linkedin_feed and package.linkedin_post_feed:
            feed_text = package.linkedin_post_feed.strip()
            if blog_url and blog_url not in feed_text:
                import re as re_feed
                hashtag_match = re_feed.search(r'((?:#\w+\s*)+)$', feed_text)
                if hashtag_match:
                    before_hash = feed_text[:hashtag_match.start()].strip()
                    hashes = hashtag_match.group(1).strip()
                    feed_text = (
                        f"{before_hash}\n\n"
                        f"Confira o ensaio completo e as tendências de mercado no blog:\n{blog_url}\n\n"
                        f"{hashes}"
                    )
                else:
                    feed_text = (
                        f"{feed_text}\n\n"
                        f"Confira o ensaio completo e as tendências de mercado no blog:\n{blog_url}"
                    )

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
                    text=feed_text,
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
                    blog_url=blog_url,
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
            pulse_status_icon = "✅" if publication_results["linkedin_pulse"].get("status") == "SUCCESS" else "⚠️"

            wpp_msg = (
                f"🎉 *Fernando, seu conteúdo revisado foi publicado com sucesso!*\n\n"
                f"📌 *Tema:* {package.titulo_blog}\n"
                f"📂 *Categoria:* {package.categoria}\n\n"
                f"{site_status_icon} *Artigo no Ar no Blog:*\n{blog_url}\n\n"
            )
            if publication_results["linkedin_pulse"].get("status") == "SUCCESS":
                wpp_msg += f"{pulse_status_icon} *Artigo no LinkedIn Pulse:* Publicado com sucesso!\n"
            elif publication_results["linkedin_pulse"].get("status") == "ERROR":
                wpp_msg += f"⚠️ *Artigo no LinkedIn Pulse:* Falha na publicação automatizada.\n"

            if publish_linkedin_feed and publication_results["linkedin_feed"].get("status") == "SUCCESS":
                wpp_msg += "✅ *LinkedIn Feed:* Post publicado com sucesso no perfil!\n"

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
            site_ok = publication_results["site"].get("status") == "SUCCESS"
            pulse_ok = publication_results["linkedin_pulse"].get("status") == "SUCCESS"

            if site_ok and pulse_ok:
                email_subject = f"✅ Conteúdo Publicado no Site e LinkedIn: {package.titulo_blog[:40]}"
                badge_text = "✓ Publicação Concluída"
                badge_bg = "rgba(34,197,94,0.2)"
                badge_color = "#86efac"
                top_border = "#16a34a"
                sub_status = "Publicado automaticamente no Blog e LinkedIn."
            elif site_ok:
                email_subject = f"✅ Conteúdo Publicado no Blog: {package.titulo_blog[:40]}"
                badge_text = "✓ Publicado no Blog"
                badge_bg = "rgba(59,130,246,0.2)"
                badge_color = "#93c5fd"
                top_border = "#2563eb"
                sub_status = "Publicado com sucesso no Blog do site."
            else:
                email_subject = f"⚠️ Falha na Publicação do Blog: {package.titulo_blog[:40]}"
                badge_text = "⚠️ Erro no Site"
                badge_bg = "rgba(239,68,68,0.2)"
                badge_color = "#fca5a5"
                top_border = "#dc2626"
                sub_status = "Falha ao gravar artigo no banco ou recompilar site."

            blog_line = (
                f'<p style="margin: 6px 0;"><strong>🌐 Blog:</strong> <a href="{html.escape(blog_url)}" target="_blank" style="color: #2563eb;">{html.escape(blog_url)}</a></p>'
                if site_ok
                else f'<p style="margin: 6px 0; color: #dc2626;"><strong>⚠️ Blog:</strong> Erro ao publicar: {html.escape(str(publication_results["site"].get("error")))}</p>'
            )

            html_body = f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #0f172a; max-width: 650px; margin: 0 auto; padding: 20px;">
                <div style="background-color: #0f172a; color: #ffffff; padding: 22px; border-radius: 10px; border-top: 4px solid {top_border};">
                    <span style="background-color: {badge_bg}; color: {badge_color}; padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: 700; text-transform: uppercase;">
                        {badge_text}
                    </span>
                    <h1 style="margin: 12px 0 4px 0; font-size: 20px;">{html.escape(package.titulo_blog)}</h1>
                    <p style="margin: 0; color: #94a3b8; font-size: 13px;">{sub_status}</p>
                </div>
                <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 18px; margin: 20px 0;">
                    {blog_line}
                    <p style="margin: 6px 0;"><strong>💼 LinkedIn:</strong> {'Artigo no Pulse publicado com sucesso' if pulse_ok else 'Ignorado / Pendente'}</p>
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

    pulse_status = result.get("publication_results", {}).get("linkedin_pulse", {}).get("status")
    site_status = result.get("publication_results", {}).get("site", {}).get("status")
    if site_status == "ERROR" or pulse_status == "ERROR":
        try:
            from redis import Redis
            r = Redis.from_url(settings.REDIS_URL)
            r.delete(f"editorial:publish_locked:{doc_id}")
            logger.info("Cleared Redis lock for doc_id %s due to publication error (site=%s, pulse=%s).", doc_id, site_status, pulse_status)
        except Exception as e_clr:
            logger.debug("Could not clear Redis publish lock: %s", e_clr)

    repo.log_flow_complete(task_id, result)
    return result
