"""
Flow: temas em alta para o dia (Daily Legal Tech & AI Editorial Curation).
Collects articles from Google News RSS feeds, deduplicates, runs Gemini AI editorial synthesis,
and dispatches formatted HTML emails via Gmail.
"""

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import feedparser
from jinja2 import Template

from core.celery_app import celery_app
from core.config import settings
from integrations.gemini import CuradoriaPautasResult, GeminiClient, PautaEditorial
from integrations.google_service import GoogleServicesClient
from storage.repository import repo

logger = logging.getLogger(__name__)

FEEDS_TI = [
    "https://news.google.com/rss/search?q=(%22tecnologia+da+informa%C3%A7%C3%A3o%22+OR+%22intelig%C3%AAncia+artificial%22+OR+%22desenvolvimento+de+software%22+OR+ciberseguran%C3%A7a+OR+cloud)&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    "https://news.google.com/rss/search?q=(%22engenharia+de+software%22+OR+DevOps+OR+%22computa%C3%A7%C3%A3o+em+nuvem%22+OR+GenAI+OR+LLM)&hl=pt-BR&gl=BR&ceid=BR:pt-419",
]

FEEDS_MUSICA = [
    "https://news.google.com/rss/search?q=(%22mercado+musical%22+OR+%22m%C3%BAsica+ao+vivo%22+OR+%22ind%C3%BAstria+musical%22+OR+%22produ%C3%A7%C3%A3o+musical%22)&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    "https://news.google.com/rss/search?q=(%22streaming+de+m%C3%BAsica%22+OR+Spotify+OR+%22shows+e+festivais%22+OR+%22voz+e+viol%C3%A3o%22+OR+ECAD)&hl=pt-BR&gl=BR&ceid=BR:pt-419",
]

EDITORIAL_SYSTEM_INSTRUCTION = """
Atue como Editor-Chefe e Estrategista de Conteúdo especializado em Tecnologia da Informação (TI) e na Indústria/Cultura da Música.

Analise as notícias recentes listadas e formule OBRIGATORIAMENTE 4 propostas de pautas editoriais para artigos, postagens e reflexões:
- Exatamente 2 pautas na categoria 'Tecnologia da Informação (TI)' (focadas em IA, desenvolvimento de software, cloud, engenharia, cibersegurança).
- Exatamente 2 pautas na categoria 'Música & Mercado Musical' (focadas em mercado da música, shows ao vivo, streaming, direitos autorais, tendências e produção musical).

Diretrizes obrigatórias:
1. Ineditismo e Não-Repetição Semanal: É terminantemente proibido propor temas idênticos ou que abordem o mesmo fato central, empresa ou desdobramento já publicado na última semana. Garanta rotação temática ativa entre os dias.
2. Agrupamento Temático: Cruze notícias que tratam de aspectos do mesmo problema ou tendência para sustentar cada pauta com mais de uma fonte, quando cabível.
3. Fidelidade Factual Estrita: No campo "sintese_fiel_das_materias", limite-se aos fatos, lançamentos, números, declarações e decisões explicitamente citados nas notícias vinculadas. Não invente dados não mencionados.
4. Utilidade de Redação: Forneça ganchos substanciais e roteiro detalhado para que o texto final possa ser redigido com autoridade.
5. Equilíbrio Rigoroso: Entregue exatamente 2 pautas de TI e 2 pautas de Música com o campo 'categoria' devidamente preenchido.
"""


def fetch_and_filter_rss_articles(
    feeds: Optional[List[str]] = None,
    cutoff_days: int = 6,
    limit: int = 25,
) -> List[Dict[str, Any]]:
    """
    Parses Google News RSS feeds, eliminates duplicates, and keeps articles published within cutoff_days.
    """
    feed_urls = feeds or (FEEDS_TI + FEEDS_MUSICA)
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


def is_topic_repetitive(
    proposed_title: str,
    recent_titles: List[str],
    threshold: float = 0.45,
) -> bool:
    """
    Evaluates whether a proposed topic title significantly overlaps with recent publication titles.
    Uses normalized content-word tokenization and Jaccard similarity.
    """
    stopwords = {
        "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "do", "da", "dos", "das",
        "em", "no", "na", "nos", "nas", "por", "para", "com", "sem", "sobre", "entre",
        "e", "ou", "mas", "que", "se", "como", "ao", "aos", "nao", "não", "mais", "sua",
        "seu", "suas", "seus", "pelo", "pela", "pelos", "pelas", "este", "esta", "esses",
        "essas", "isto", "isso", "aquilo", "papel", "futuro", "desafio", "alerta",
    }

    def tokenize(text: str) -> set:
        words = re.findall(r"\b[a-zA-Z0-9áéíóúâêîôûãõçÁÉÍÓÚÂÊÎÔÛÃÕÇ]{3,}\b", (text or "").lower())
        return {w for w in words if w not in stopwords}

    proposed_tokens = tokenize(proposed_title)
    if not proposed_tokens:
        return False

    for past_title in recent_titles:
        past_tokens = tokenize(past_title)
        if not past_tokens:
            continue
        intersection = proposed_tokens.intersection(past_tokens)
        union = proposed_tokens.union(past_tokens)
        similarity = len(intersection) / len(union) if union else 0.0
        overlap_ratio = len(intersection) / len(proposed_tokens)

        if similarity >= threshold or overlap_ratio >= 0.55:
            logger.warning(
                "Proposed topic '%s' is repetitive against past publication '%s' (similarity: %.2f, overlap: %.2f)",
                proposed_title,
                past_title,
                similarity,
                overlap_ratio,
            )
            return True

    return False


def render_pautas_email_html(
    pautas: List[PautaEditorial],
    recent_publications_count: int = 0,
) -> str:
    """
    Renders Jinja2 HTML email template with anti-repetition memory indicator.
    """
    template_path = Path("/app/templates/pautas_email.html")
    if not template_path.exists():
        template_path = Path("templates/pautas_email.html")

    with open(template_path, "r", encoding="utf-8") as f:
        tmpl = Template(f.read())

    return tmpl.render(
        pautas=pautas,
        recent_publications_count=recent_publications_count,
    )


@celery_app.task(name="flows.flow_editorial_pautas.task_daily_editorial_curation")
def task_daily_editorial_curation() -> Dict[str, Any]:
    """
    Periodic task running daily at 07:00 BRT to generate 2 IT topics and 2 Music topics.
    Applies persistent editorial memory (last 7 days) to strictly avoid topical repetition.
    """
    task_id = "daily_pautas_" + datetime.now().strftime("%Y%m%d")
    logger.info("Starting Daily Editorial Curation pipeline with Anti-Repetition memory...")
    repo.log_flow_start(task_id, "daily_editorial_curation", {})

    # 0. Retrieve recent editorial publications (last 7 days) to enforce anti-repetition memory
    recent_pubs = repo.get_recent_editorial_publications(days=7)
    recent_titles = [p.get("tema", "") for p in recent_pubs if p.get("tema")]
    logger.info(
        "Found %d recent publication(s) in the last 7 days for anti-repetition memory.",
        len(recent_pubs),
    )

    # 1. Fetch and filter articles for both categories
    articles_ti = fetch_and_filter_rss_articles(feeds=FEEDS_TI, limit=25)
    articles_musica = fetch_and_filter_rss_articles(feeds=FEEDS_MUSICA, limit=25)

    if not articles_ti and not articles_musica:
        logger.warning("No articles found in RSS feeds for the specified window.")
        return {"status": "NO_ARTICLES", "pautas": []}

    formatted_ti = "\n".join(
        [f"{i+1}. [{a['date']}] [{a['source']}] {a['title']}" for i, a in enumerate(articles_ti)]
    )
    formatted_musica = "\n".join(
        [f"{i+1}. [{a['date']}] [{a['source']}] {a['title']}" for i, a in enumerate(articles_musica)]
    )

    prompt_sections = [
        "Resumo das notícias recentes encontradas nos últimos 6 dias divididas por área:\n",
        "=== 💻 NOTÍCIAS DE TECNOLOGIA DA INFORMAÇÃO (TI) ===",
        formatted_ti,
        "\n=== 🎵 NOTÍCIAS DE MÚSICA & MERCADO MUSICAL ===",
        formatted_musica,
    ]

    if recent_pubs:
        formatted_recent = "\n".join(
            [
                f"• [{p.get('created_at', '')[:10]}] [{p.get('categoria', '')}] \"{p.get('tema', '')}\""
                + (f" (Ângulo: {p.get('angulo_editorial')})" if p.get("angulo_editorial") else "")
                for p in recent_pubs
            ]
        )
        prompt_sections.extend([
            "\n=== 🚫 HISTÓRICO DE PUBLICAÇÕES RECENTES (ÚLTIMOS 7 DIAS - VETO TOTAL A REPETIÇÕES) ===",
            "Os seguintes temas e ganchos JÁ FORAM PRODUZIDOS E PUBLICADOS nesta semana:",
            formatted_recent,
            "\nDIRETRIZES DE NÃO-REPETIÇÃO E ROTAÇÃO TEMÁTICA OBRIGATÓRIAS:",
            "1. É ESTRITAMENTE PROIBIDO sugerir pautas que repitam o mesmo assunto central, empresa foco ou acontecimento já coberto acima.",
            "2. Se uma empresa ou tema (ex: Spotify, regulação eleitoral de IA, etc.) já foi publicado recentemente, explore OBRIGATORIAMENTE outros eixos da categoria:",
            "   - Em 'Música & Mercado Musical': alterne entre shows ao vivo, festivais, mercado fonográfico/indústria, ECAD/direitos autorais, acústica, voz e violão, IA na produção musical ou turnês.",
            "   - Em 'Tecnologia da Informação (TI)': alterne entre arquitetura de software, cibersegurança prática, DevOps/SRE, computação em nuvem, bancos de dados, linguagens de programação ou IA aplicada a negócios.",
        ])

    prompt_sections.extend([
        "\nCom base rigorosa nos acontecimentos acima e RESPEITANDO O VETO A TEMAS JÁ PUBLICADOS, elabore EXATAMENTE 4 pautas:",
        "- 2 pautas de Tecnologia da Informação (TI)",
        "- 2 pautas de Música & Mercado Musical",
        "Preencha o campo 'categoria' de cada pauta com o nome exato correspondente."
    ])

    prompt = "\n".join(prompt_sections)

    # 2. Curate with Gemini
    gemini = GeminiClient()
    curadoria: CuradoriaPautasResult = gemini.generate_structured(
        prompt=prompt,
        system_instruction=EDITORIAL_SYSTEM_INSTRUCTION,
        response_model=CuradoriaPautasResult,
        model_name="gemini-2.5-flash",
    )

    # 2.1 Guardrail de Similaridade Algorítmica: auditar pautas propostas contra o histórico da semana
    repetitive_count = 0
    if recent_titles:
        for pauta in curadoria.pautas:
            if is_topic_repetitive(pauta.titulo, recent_titles):
                repetitive_count += 1
                logger.warning(
                    "Topic Guardrail Flag: Pauta #%s ('%s') has strong semantic overlap with recent publications.",
                    pauta.id,
                    pauta.titulo,
                )

    # 3. Generate signed action tokens for one-click Blog + LinkedIn drafting
    from core.security import create_editorial_action_token
    base_url = "https://www.fernandonogueira.dev.br"
    today_ymd = datetime.now().strftime("%Y%m%d")
    for pauta in curadoria.pautas:
        token = create_editorial_action_token(
            pauta_id=pauta.id,
            pauta_titulo=pauta.titulo,
            categoria=pauta.categoria,
            target_format="both",
            angulo_editorial=pauta.angulo_editorial,
            curation_date=today_ymd,
        )
        pauta.action_url = f"{base_url}/api/v1/editorial/select?token={token}"

    # 4. Render and Send HTML Email with Memory Badge
    email_html = render_pautas_email_html(
        pautas=curadoria.pautas,
        recent_publications_count=len(recent_pubs),
    )
    google_svc = GoogleServicesClient()
    today_str = datetime.now().strftime("%d/%m/%Y")
    subject = f"⚡ 4 Pautas do Dia (2 TI & 2 Música) - {today_str}"

    google_svc.send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=subject,
        html_body=email_html,
    )
    logger.info("Editorial curation email (4 pautas: TI & Música) dispatched to %s", settings.ADMIN_EMAIL)

    total_articles = len(articles_ti) + len(articles_musica)
    final_result = {
        "status": "SUCCESS",
        "total_articles_analyzed": total_articles,
        "total_pautas_generated": len(curadoria.pautas),
        "recent_publications_considered": len(recent_pubs),
        "repetitive_flags_detected": repetitive_count,
        "pautas": [p.model_dump() for p in curadoria.pautas],
    }
    repo.log_flow_complete(task_id, final_result)
    return final_result
