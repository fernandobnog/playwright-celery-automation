"""
Automated unit tests for the Daily Editorial Pautas Flow.
Tests RSS parsing, deduplication, Jinja2 email rendering, and Celery curation task.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

from flows.flow_editorial_pautas import (
    fetch_and_filter_rss_articles,
    render_pautas_email_html,
    task_daily_editorial_curation,
)
from integrations.gemini import CuradoriaPautasResult, FonteNoticia, PautaEditorial


def test_fetch_and_filter_rss_articles():
    now_tuple = datetime.now(timezone.utc).timetuple()[:6]

    mock_feed = MagicMock()
    mock_feed.entries = [
        {
            "title": "STF regulamenta IA nos tribunais - ConJur",
            "link": "https://conjur.com.br/artigo1",
            "published_parsed": now_tuple,
            "source": {"title": "ConJur"},
        },
        # Duplicate with slight suffix difference
        {
            "title": "STF regulamenta IA nos tribunais - G1",
            "link": "https://g1.globo.com/artigo1",
            "published_parsed": now_tuple,
            "source": {"title": "G1"},
        },
        # Different article
        {
            "title": "Prompt Injection em processos judiciais",
            "link": "https://migalhas.com.br/artigo2",
            "published_parsed": now_tuple,
            "source": {"title": "Migalhas"},
        },
    ]

    with patch("feedparser.parse", return_value=mock_feed):
        articles = fetch_and_filter_rss_articles(feeds=["http://mock-rss"])
        assert len(articles) == 2  # Deduplicated from 3
        titles = [a["title"] for a in articles]
        assert "STF regulamenta IA nos tribunais" in titles
        assert "Prompt Injection em processos judiciais" in titles


def test_render_pautas_email_html():
    pautas = [
        PautaEditorial(
            id=1,
            titulo="Nova Regulamentação de IA no CNJ",
            angulo_editorial="Tribunais devem auditar modelos preditivos",
            fontes_relacionadas=[
                FonteNoticia(id_noticia=1, veiculo="ConJur", data="17/09/2026", titulo_original="CNJ edita norma")
            ],
            sintese_fiel_das_materias="O CNJ estabeleceu regras para auditoria algorítmica.",
            topicos_para_redacao=["Contexto", "Implicações", "Conclusão"],
        )
    ]

    html = render_pautas_email_html(pautas)
    assert "Nova Regulamentação de IA no CNJ" in html
    assert "Tribunais devem auditar modelos preditivos" in html
    assert "PAUTA #1" in html
    assert "Omni-Flow Python Engine" in html


def test_task_daily_editorial_curation():
    mock_curadoria = CuradoriaPautasResult(
        pautas=[
            PautaEditorial(
                id=1,
                titulo="Responsabilidade Civil e Alucinações de IA",
                angulo_editorial="Como advogados estão respondendo por jurisprudência falsa",
                fontes_relacionadas=[],
                sintese_fiel_das_materias="Casos recentes apontam aplicação de litigância de má-fé.",
                topicos_para_redacao=["O caso prático", "Precedentes"],
            )
        ]
    )

    with patch("flows.flow_editorial_pautas.fetch_and_filter_rss_articles") as mock_rss, \
         patch("integrations.gemini.GeminiClient.generate_structured", return_value=mock_curadoria), \
         patch("integrations.google_service.GoogleServicesClient.send_email") as mock_send_email, \
         patch("storage.repository.repo.log_flow_start"), \
         patch("storage.repository.repo.log_flow_complete"):

        mock_rss.return_value = [
            {"title": "Advogado multado por citar IA com julgado falso", "date": "16/09/2026", "source": "Migalhas"}
        ]

        result = task_daily_editorial_curation()
        assert result["status"] == "SUCCESS"
        assert result["total_pautas_generated"] == 1
        mock_send_email.assert_called_once()
