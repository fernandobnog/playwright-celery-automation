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

    html = render_pautas_email_html(pautas, recent_publications_count=3)
    assert "Nova Regulamentação de IA no CNJ" in html
    assert "Tribunais devem auditar modelos preditivos" in html
    assert "PAUTA #1" in html
    assert "Omni-Flow Python Engine" in html
    assert "Memória Editorial: 3 publicação(ões)" in html


def test_is_topic_repetitive():
    from flows.flow_editorial_pautas import is_topic_repetitive

    recent_titles = [
        "Música e Entretenimento Cruzado: O Papel do Spotify nas Campanhas de Marketing de GTA 6 e Stranger Things",
        "A Ofensiva do Vídeo no Spotify: Como a Monetização de Videocasts e Clipes Miram a Recuperação de Valor",
        "Inteligência Artificial: Entre o Potencial Transformador e os Desafios Éticos e Regulatórios",
    ]

    # Clearly repetitive against Spotify videocasts
    assert is_topic_repetitive(
        "A Estratégia do Spotify e Videocasts na Monetização de Criadores",
        recent_titles,
    ) is True

    # Clearly repetitive against AI Regulation
    assert is_topic_repetitive(
        "Inteligência Artificial e os Desafios Éticos e Regulatórios Urgentes",
        recent_titles,
    ) is True

    # Fresh, completely distinct topics should pass
    assert is_topic_repetitive(
        "Engenharia de Confiabilidade (SRE): Lições de Resiliência em Kubernetes e Cloud",
        recent_titles,
    ) is False

    assert is_topic_repetitive(
        "Turnês Acústicas e a Retomada dos Festivais Independentes no Brasil",
        recent_titles,
    ) is False


def test_editorial_publications_repository(tmp_path):
    from storage.repository import PipelineRepository

    test_db = tmp_path / "test_repo.db"
    with patch("psycopg2.connect", side_effect=Exception("Disabled in test")):
        repo_inst = PipelineRepository(db_path=str(test_db))

    assert repo_inst.get_recent_editorial_publications(days=7) == []

    repo_inst.record_editorial_publication(
        task_id="test_task_1",
        tema="Cibersegurança e Ransomware em Infraestruturas Críticas",
        categoria="Tecnologia da Informação (TI)",
        angulo_editorial="Como empresas de energia estão mitigando riscos",
        doc_id="doc123",
        doc_url="https://docs.google.com/doc123",
    )

    recent = repo_inst.get_recent_editorial_publications(days=7)
    assert len(recent) == 1
    assert recent[0]["tema"] == "Cibersegurança e Ransomware em Infraestruturas Críticas"
    assert recent[0]["categoria"] == "Tecnologia da Informação (TI)"
    assert recent[0]["doc_id"] == "doc123"


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
         patch("storage.repository.repo.get_recent_editorial_publications", return_value=[{"tema": "Tema Antigo", "categoria": "TI"}]), \
         patch("storage.repository.repo.log_flow_start"), \
         patch("storage.repository.repo.log_flow_complete"):

        mock_rss.return_value = [
            {"title": "Advogado multado por citar IA com julgado falso", "date": "16/09/2026", "source": "Migalhas"}
        ]

        result = task_daily_editorial_curation()
        assert result["status"] == "SUCCESS"
        assert result["total_pautas_generated"] == 1
        assert result["recent_publications_considered"] == 1
        mock_send_email.assert_called_once()

