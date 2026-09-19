"""
Unit Tests for Content Publisher Pipeline (Phase 1: Site API + LinkedIn Publisher).
Validates Google Docs text extraction, SitePublisher direct database ops,
LinkedInPublisher with shared storage state, HMAC publish tokens, and the FastAPI /publish endpoint.
"""

from datetime import datetime
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
import pytest

from api.main import app
from core.security import create_editorial_publish_token, verify_editorial_publish_token
from flows.flow_content_publisher import (
    ReviewedContentPackage,
    parse_reviewed_doc_content,
    publish_reviewed_editorial,
)
from integrations.google.docs import DocsService
from integrations.site_publisher import SitePublisher, slugify
from scrapers.linkedin_publisher import LinkedInPublisher


# ==============================================================================
# 1. SitePublisher & Slugify Tests
# ==============================================================================
def test_slugify():
    assert slugify("Inteligência Artificial & Governança no Brasil!") == "inteligencia-artificial-governanca-no-brasil"
    assert slugify("Música ao Vivo: Voz e Violão") == "musica-ao-vivo-voz-e-violao"
    assert slugify("DevOps & Cloud 2026") == "devops-cloud-2026"


def test_site_publisher_insert_post():
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # Simulate post doesn't exist yet (cur.fetchone() returns None on SELECT, then (10, 'slug') on INSERT RETURNING)
    mock_cur.fetchone.side_effect = [None, (10, "ia-na-pratica")]

    publisher = SitePublisher(postgres_url="postgresql://user:pass@localhost:5432/db")
    with patch.object(publisher, "_get_connection", return_value=mock_conn):
        result = publisher.publish_post(
            title="IA na Prática",
            content="# Artigo completo...",
            excerpt="Resumo do artigo",
            category="Tecnologia da Informação",
            tags=["IA", "Software"],
            read_time=6,
            slug="ia-na-pratica",
        )

    assert result["status"] == "SUCCESS"
    assert result["post_id"] == 10
    assert result["slug"] == "ia-na-pratica"
    assert "https://www.fernandonogueira.dev.br/blog/ia-na-pratica" in result["url"]
    assert mock_cur.execute.call_count >= 2


def test_site_publisher_update_existing_post():
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # Simulate post already exists with ID 42
    mock_cur.fetchone.return_value = (42,)

    publisher = SitePublisher(postgres_url="postgresql://user:pass@localhost:5432/db")
    with patch.object(publisher, "_get_connection", return_value=mock_conn):
        result = publisher.publish_post(
            title="IA na Prática Atualizado",
            content="# Novo conteúdo...",
            excerpt="Novo resumo",
            category="Música & Mercado Musical",
            slug="ia-na-pratica",
        )

    assert result["status"] == "SUCCESS"
    assert result["post_id"] == 42
    assert "blog/ia-na-pratica" in result["url"]


# ==============================================================================
# 2. DocsService Text Extraction Tests
# ==============================================================================
def test_docs_service_extract_text_content():
    mock_auth = MagicMock()
    docs_service = DocsService(auth_manager=mock_auth)

    fake_doc = {
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {"textRun": {"content": "Título do Artigo\n"}},
                            {"textRun": {"content": "Subtítulo explicativo\n"}},
                        ]
                    }
                },
                {
                    "table": {
                        "tableRows": [
                            {
                                "tableCells": [
                                    {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Coluna 1"}}]}}]},
                                    {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Coluna 2"}}]}}]},
                                ]
                            }
                        ]
                    }
                },
                {
                    "paragraph": {
                        "elements": [
                            {"textRun": {"content": "Parágrafo final com [Link](https://site.com)\n"}}
                        ]
                    }
                },
            ]
        }
    }

    with patch.object(docs_service, "get_document", return_value=fake_doc):
        extracted = docs_service.extract_text_content("test-doc-id")

    assert "Título do Artigo" in extracted
    assert "Subtítulo explicativo" in extracted
    assert "Coluna 1 | Coluna 2" in extracted
    assert "Parágrafo final com [Link](https://site.com)" in extracted


# ==============================================================================
# 3. Security Publish Token Tests
# ==============================================================================
def test_create_and_verify_editorial_publish_token():
    token = create_editorial_publish_token(
        doc_id="doc_12345_abc",
        pauta_titulo="Revolução dos Agentes de IA",
        categoria="Tecnologia da Informação (TI)",
        doc_url="https://docs.google.com/document/d/doc_12345_abc/edit",
        expires_in_seconds=3600,
    )

    assert isinstance(token, str)
    assert token.startswith("pub_")

    payload = verify_editorial_publish_token(token)
    assert payload is not None
    assert payload["action"] == "publish_phase1"
    assert payload["doc_id"] == "doc_12345_abc"
    assert payload["pauta_titulo"] == "Revolução dos Agentes de IA"
    assert payload["categoria"] == "Tecnologia da Informação (TI)"
    assert payload["doc_url"] == "https://docs.google.com/document/d/doc_12345_abc/edit"


def test_create_and_verify_editorial_publish_token_hmac_fallback():
    # Force Redis failure to test HMAC fallback token format
    with patch("redis.Redis.from_url", side_effect=Exception("Redis offline")):
        token = create_editorial_publish_token(
            doc_id="doc_fallback_123",
            pauta_titulo="Fallback HMAC Token",
            categoria="TI",
            doc_url="https://docs.google.com/document/d/doc_fallback_123/edit",
            expires_in_seconds=3600,
        )

    assert isinstance(token, str)
    assert "." in token

    # Verification with Redis offline should still succeed using HMAC
    with patch("redis.Redis.from_url", side_effect=Exception("Redis offline")):
        payload = verify_editorial_publish_token(token)

    assert payload is not None
    assert payload["doc_id"] == "doc_fallback_123"
    assert payload["pauta_titulo"] == "Fallback HMAC Token"


def test_verify_editorial_publish_token_invalid_or_expired():
    assert verify_editorial_publish_token("") is None
    assert verify_editorial_publish_token("invalid.token.signature") is None

    # Expired short token
    expired_token = create_editorial_publish_token(
        doc_id="doc_expired",
        pauta_titulo="Tema Expirado",
        categoria="TI",
        expires_in_seconds=-10,
    )
    assert verify_editorial_publish_token(expired_token) is None


# ==============================================================================
# 4. LinkedIn Publisher State & Sync Tests
# ==============================================================================
def test_linkedin_publisher_sync_session(tmp_path):
    state_file = tmp_path / "linkedin_state.json"
    publisher = LinkedInPublisher(state_path=str(state_file))

    # Without redis, file doesn't exist
    with patch.object(publisher, "_get_redis", return_value=None):
        assert publisher.sync_session_from_redis_or_disk() is None

        # Write sample file
        state_file.write_text('{"cookies": [{"name": "li_at", "value": "xyz"}]}', encoding="utf-8")
        assert publisher.sync_session_from_redis_or_disk() == str(state_file)


def test_linkedin_publisher_save_session(tmp_path):
    state_file = tmp_path / "linkedin_state.json"
    publisher = LinkedInPublisher(state_path=str(state_file))

    mock_context = MagicMock()
    mock_redis = MagicMock()

    with patch.object(publisher, "_get_redis", return_value=mock_redis):
        # Create state file so read_text works
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text('{"cookies": []}')

        publisher.save_session_to_disk_and_redis(mock_context)

        mock_context.storage_state.assert_called_once_with(path=str(state_file))
        mock_redis.set.assert_called_once()


# ==============================================================================
# 5. Flow Content Publisher Orchestration Tests
# ==============================================================================
def test_parse_reviewed_doc_content():
    mock_gemini = MagicMock()
    mock_package = ReviewedContentPackage(
        titulo_blog="O Fim do PDF Inofensivo",
        subtitulo_blog="Como a segurança de IA mudou a análise de documentos corporativos",
        slug_blog="o-fim-do-pdf-inofensivo",
        corpo_blog_markdown="## Casos Reais\nO tribunal identificou injeção de prompt...",
        meta_description="Descubra os riscos do prompt injection indireto em arquivos PDF.",
        categoria="TECNOLOGIA",
        tags=["Cibersegurança", "IA"],
        tempo_leitura_minutos=4,
        linkedin_post_feed="Você ainda abre PDFs sem desconfiar? 👇\n\nNesta semana...",
        linkedin_artigo_titulo="A Nova Fronteira da Cibersegurança em IA",
        linkedin_artigo_corpo="# A Nova Fronteira...\n\nA governança de dados...",
    )
    mock_gemini.generate_structured.return_value = mock_package

    raw_text = "CONTEÚDO DO GOOGLE DOCS REVISADO..."
    result = parse_reviewed_doc_content(raw_text, gemini_client=mock_gemini)

    assert result.titulo_blog == "O Fim do PDF Inofensivo"
    assert result.slug_blog == "o-fim-do-pdf-inofensivo"
    assert "👇" in result.linkedin_post_feed
    assert result.linkedin_artigo_titulo == "A Nova Fronteira da Cibersegurança em IA"


def test_publish_reviewed_editorial_orchestration():
    mock_hub = MagicMock()
    mock_site_pub = MagicMock()
    mock_linkedin_pub = MagicMock()
    mock_evo = MagicMock()

    mock_hub.docs.extract_text_content.return_value = "Texto revisado do Google Docs com mais de cinquenta caracteres obrigatórios."

    mock_package = ReviewedContentPackage(
        titulo_blog="IA na Advocacia 2026",
        subtitulo_blog="O Advogado Maestro",
        slug_blog="ia-na-advocacia-2026",
        corpo_blog_markdown="Artigo do blog...",
        meta_description="Meta desc",
        categoria="TECNOLOGIA",
        tags=["Direito", "IA"],
        tempo_leitura_minutos=5,
        linkedin_post_feed="Post do LinkedIn feed 👇",
        linkedin_artigo_titulo="Artigo LinkedIn Pulse",
        linkedin_artigo_corpo="Corpo LinkedIn Pulse",
    )

    mock_site_pub.publish_post.return_value = {
        "status": "SUCCESS",
        "post_id": 99,
        "slug": "ia-na-advocacia-2026",
        "url": "https://www.fernandonogueira.dev.br/blog/ia-na-advocacia-2026",
    }

    mock_linkedin_pub.publish_feed_post.return_value = {
        "status": "SUCCESS",
        "channel": "linkedin_feed",
    }
    mock_linkedin_pub.publish_pulse_article.return_value = {
        "status": "SUCCESS",
        "channel": "linkedin_article",
    }
    mock_evo.send_text_message = AsyncMock(return_value={"status": "SENT"})

    with patch("flows.flow_content_publisher.parse_reviewed_doc_content", return_value=mock_package), \
         patch("flows.flow_content_publisher.repo") as mock_repo:

        res = publish_reviewed_editorial(
            doc_id="test_doc_abc",
            pauta_titulo="IA na Advocacia 2026",
            categoria="TECNOLOGIA",
            doc_url="https://docs.google.com/document/d/test_doc_abc/edit",
            hub=mock_hub,
            site_pub=mock_site_pub,
            linkedin_pub=mock_linkedin_pub,
            evo=mock_evo,
        )

    assert res["status"] == "SUCCESS"
    assert res["slug"] == "ia-na-advocacia-2026"
    assert res["blog_url"] == "https://www.fernandonogueira.dev.br/blog/ia-na-advocacia-2026"
    assert res["publication_results"]["site"]["status"] == "SUCCESS"
    assert res["publication_results"]["linkedin_feed"]["status"] == "SKIPPED"
    assert res["publication_results"]["linkedin_pulse"]["status"] == "SUCCESS"
    mock_linkedin_pub.publish_feed_post.assert_not_called()
    mock_linkedin_pub.publish_pulse_article.assert_called_once()
    mock_repo.record_editorial_publication.assert_called_once()


# ==============================================================================
# 6. FastAPI Endpoint Tests (/api/v1/editorial/publish)
# ==============================================================================
def test_editorial_publish_endpoint_valid_token():
    client = TestClient(app)
    token = create_editorial_publish_token(
        doc_id="doc_xyz_789",
        pauta_titulo="Governança de IA",
        categoria="Tecnologia da Informação (TI)",
        doc_url="https://docs.google.com/document/d/doc_xyz_789/edit",
    )

    with patch("api.routes.editorial.task_publish_approved_editorial.delay") as mock_task, \
         patch("api.routes.editorial._get_redis", return_value=None):
        mock_task.return_value = MagicMock(id="async_task_publish_999")

        response = client.get(f"/api/v1/editorial/publish?token={token}")

    assert response.status_code == 200
    assert "Publicação Iniciada!" in response.text
    assert "Governança de IA" in response.text
    assert "Publicação no Blog Oficial" in response.text
    assert "Postagem no LinkedIn" in response.text
    mock_task.assert_called_once()


def test_editorial_publish_endpoint_invalid_token():
    client = TestClient(app)
    response = client.get("/api/v1/editorial/publish?token=invalid_hmac_token")
    assert response.status_code == 400
    assert "Link Não Reconhecido" in response.text or "Link Expirado ou Inválido" in response.text
