"""
Flow: temas em alta para o dia (Daily Legal Tech & AI Editorial Curation).
Collects articles from Google News RSS feeds, deduplicates, runs Gemini AI editorial synthesis,
and dispatches formatted HTML emails via Gmail.
"""

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import feedparser
from jinja2 import Template

from core.celery_app import celery_app
from core.config import settings
from integrations.gemini import CuradoriaPautasResult, GeminiClient, PautaEditorial
from integrations.google_service import GoogleServicesClient
from storage.repository import repo

logger = logging.getLogger(__name__)

GOOGLE_NEWS_FEEDS = [
    "https://news.google.com/rss/search?q=(%22intelig%C3%AAncia+artificial%22+OR+IA+OR+genai)+AND+(jur%C3%ADdico+OR+advogado+OR+%22escrit%C3%B3rio+de+advocacia%22)&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    "https://news.google.com/rss/search?q=(%22intelig%C3%AAncia+artificial%22+OR+IA+OR+algoritmo)+AND+(CNJ+OR+STF+OR+STJ+OR+%22tribunal+de+justi%C3%A7a%22+OR+%22judici%C3%A1rio%22)&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    "https://news.google.com/rss/search?q=(%22prompt+injection%22+OR+%22marco+legal+da+IA%22+OR+%22jurisprud%C3%AAncia+falsa%22+OR+%22alucina%C3%A7%C3%A3o%22+OR+%22discrimina%C3%A7%C3%A3o+algor%C3%ADtmica%22)+AND+(direito+OR+jur%C3%ADdico+OR+processo)&hl=pt-BR&gl=BR&ceid=BR:pt-419",
]

EDITORIAL_SYSTEM_INSTRUCTION = """
Atue como Editor-Chefe e Estrategista de Conteúdo especializado no mercado Jurídico e Legal Tech.

Analise as notícias recentes listadas, identifique os tópicos mais relevantes e formule exatamente 2 propostas de pautas editoriais para a redação de artigos e análises aprofundadas.

Diretrizes obrigatórias:
1. Agrupamento Temático: Cruze notícias que tratam de aspectos do mesmo problema para sustentar cada pauta com mais de uma fonte, quando cabível.
2. Fidelidade Factual Estrita: No campo "sintese_fiel_das_materias", limite-se aos fatos, decisões, casos e números explicitamente citados nas notícias vinculadas. Não invente premissas ou jurisprudências não mencionadas.
3. Utilidade de Redação: Forneça substância e ganchos suficientes para que o texto final possa ser redigido sem a necessidade de reabrir a lista de notícias.
"""


def fetch_and_filter_rss_articles(
    feeds: Optional[List[str]] = None,
    cutoff_days: int = 6,
    limit: int = 35,
) -> List[Dict[str, Any]]:
    """
    Parses Google News RSS feeds, eliminates duplicates, and keeps articles published within cutoff_days.
    """
    feed_urls = feeds or GOOGLE_NEWS_FEEDS
    cutoff = datetime.now(timezone.utc) - timedelta(days=cutoff_days)
    seen_titles = set()
    articles = []

    for url in feed_urls:
        parsed = feedparser.parse(url)
        for entry in parsed.entries:
            title = entry.get("title", "")
            # Clean trailing publication source (e.g. "Title - G1")
            clean_title = title.split(" - ")[0].strip() if " - " in title else title.strip()
            if not clean_title or clean_title.lower() in seen_titles:
                continue

            # Parse publication timestamp
            pub_tuple = entry.get("published_parsed")
            if pub_tuple:
                pub_date = datetime(*pub_tuple[:6], tzinfo=timezone.utc)
                if pub_date < cutoff:
                    continue
            else:
                pub_date = datetime.now(timezone.utc)

            seen_titles.add(clean_title.lower())
            articles.append({
                "title": clean_title,
                "link": entry.get("link", ""),
                "source": entry.get("source", {}).get("title", "Google News"),
                "date": pub_date.strftime("%d/%m/%Y %H:%M"),
                "timestamp": pub_date.timestamp(),
            })

    # Sort descending by date
    articles.sort(key=lambda x: x["timestamp"], reverse=True)
    return articles[:limit]


def render_pautas_email_html(pautas: List[PautaEditorial]) -> str:
    """
    Renders Jinja2 HTML email template.
    """
    template_path = Path("/app/templates/pautas_email.html")
    if not template_path.exists():
        template_path = Path("templates/pautas_email.html")

    with open(template_path, "r", encoding="utf-8") as f:
        tmpl = Template(f.read())

    return tmpl.render(pautas=pautas)


@celery_app.task(name="flows.flow_editorial_pautas.task_daily_editorial_curation")
def task_daily_editorial_curation() -> Dict[str, Any]:
    """
    Periodic task running daily at 07:00 BRT to generate content topics for the day.
    """
    task_id = "daily_pautas_" + datetime.now().strftime("%Y%m%d")
    logger.info("Starting Daily Editorial Curation pipeline...")
    repo.log_flow_start(task_id, "daily_editorial_curation", {})

    # 1. Fetch and filter articles
    articles = fetch_and_filter_rss_articles()
    if not articles:
        logger.warning("No articles found in RSS feeds for the specified window.")
        return {"status": "NO_ARTICLES", "pautas": []}

    formatted_text = "\n".join(
        [f"{i+1}. [{a['date']}] [{a['source']}] {a['title']}" for i, a in enumerate(articles)]
    )

    # 2. Curate with Gemini
    gemini = GeminiClient()
    prompt = f"Resumo das notícias recentes encontradas nos últimos 6 dias:\n\n{formatted_text}"
    curadoria: CuradoriaPautasResult = gemini.generate_structured(
        prompt=prompt,
        system_instruction=EDITORIAL_SYSTEM_INSTRUCTION,
        response_model=CuradoriaPautasResult,
        model_name="gemini-2.5-flash",
    )

    # 3. Render and Send HTML Email
    email_html = render_pautas_email_html(curadoria.pautas)
    google_svc = GoogleServicesClient()
    today_str = datetime.now().strftime("%d/%m/%Y")
    subject = f"⚡ {len(curadoria.pautas)} Pautas de Conteúdo Selecionadas - {today_str}"

    google_svc.send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=subject,
        html_body=email_html,
    )
    logger.info("Editorial curation email successfully dispatched to %s", settings.ADMIN_EMAIL)

    final_result = {
        "status": "SUCCESS",
        "total_articles_analyzed": len(articles),
        "total_pautas_generated": len(curadoria.pautas),
        "pautas": [p.model_dump() for p in curadoria.pautas],
    }
    repo.log_flow_complete(task_id, final_result)
    return final_result
