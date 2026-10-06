"""
Unit tests for LinkedIn Sniper Growth flow, repository methods, and security tokens.
"""

from unittest.mock import MagicMock, patch
import pytest

from core.security import (
    create_linkedin_comment_action_token,
    verify_linkedin_comment_action_token,
)
from flows.flow_linkedin_growth import (
    SniperCommentResult,
    generate_sniper_comment,
)
from storage.repository import PipelineRepository


def test_linkedin_comment_token_lifecycle():
    token = create_linkedin_comment_action_token(
        comment_id=999,
        post_url="https://www.linkedin.com/feed/update/urn:li:activity:123456789/",
        target_nome="Daniel Becker",
        expires_in_seconds=3600,
    )
    assert token is not None
    assert len(token) > 5

    payload = verify_linkedin_comment_action_token(token)
    assert payload is not None
    assert payload["comment_id"] == 999
    assert payload["target_nome"] == "Daniel Becker"
    assert "https://www.linkedin.com/feed/update/urn:li:activity:123456789/" in payload["post_url"]


def test_invalid_linkedin_comment_token():
    assert verify_linkedin_comment_action_token("") is None
    assert verify_linkedin_comment_action_token("invalid.token.signature") is None


def test_repository_linkedin_growth_lifecycle(tmp_path):
    db_file = tmp_path / "test_growth.db"
    repo = PipelineRepository(db_path=str(db_file))

    # Check targets seeded automatically
    targets = repo.get_active_linkedin_targets()
    assert len(targets) >= 12
    assert any(t["nome"] == "Daniel Becker" for t in targets)
    assert any(t["nome"] == "Patrícia Peck" for t in targets)
    assert any(t["nome"] == "Léo Oliveira" for t in targets)

    # Save a test comment
    comment_id = repo.save_linkedin_growth_comment(
        target_id=targets[0]["id"],
        target_nome=targets[0]["nome"],
        post_url="https://www.linkedin.com/feed/update/urn:li:activity:99999/",
        post_texto="Texto do post sobre IA nos escritórios...",
        post_autor="Daniel Becker",
        comentario_gerado="Excelente provocação sobre custos ocultos...",
        action_token="test_token_123",
    )
    assert comment_id is not None

    # Retrieve comment by ID
    c_by_id = repo.get_linkedin_growth_comment_by_id(comment_id)
    assert c_by_id is not None
    assert c_by_id["status"] == "PENDING_APPROVAL"
    assert c_by_id["target_nome"] == targets[0]["nome"]

    # Retrieve by token
    c_by_token = repo.get_linkedin_growth_comment_by_token("test_token_123")
    assert c_by_token is not None
    assert c_by_token["id"] == comment_id

    # Update status to PUBLISHED
    updated = repo.update_linkedin_growth_comment_status(comment_id, "PUBLISHED")
    assert updated is True

    c_published = repo.get_linkedin_growth_comment_by_id(comment_id)
    assert c_published["status"] == "PUBLISHED"
    assert c_published["published_at"] is not None

    # Stats verification
    stats = repo.get_linkedin_growth_stats()
    assert stats["total_targets"] >= 12
    assert stats["published_comments"] >= 1


def test_generate_sniper_comment_mocked():
    mock_gemini = MagicMock()
    mock_gemini.generate_structured.return_value = SniperCommentResult(
        comentario="Na prática, o débito técnico surge quando automatizamos sem governança. Testamos isso na bancada.",
        tese_central="IA em LegalOps exige cuidado",
        angulo_utilizado="contraponto técnico",
    )

    res = generate_sniper_comment(
        post_text="Muitos escritórios estão usando IA generativa para redigir contratos.",
        author_name="Daniel Becker",
        nicho="TI_JURIDICO",
        gemini_client=mock_gemini,
    )

    assert "débito técnico" in res.comentario
    assert res.angulo_utilizado == "contraponto técnico"
    mock_gemini.generate_structured.assert_called_once()


def test_style_memory_and_comment_editing(tmp_path):
    from flows.flow_linkedin_growth import refine_sniper_comment
    db_file = tmp_path / "test_memory.db"
    test_repo = PipelineRepository(db_path=str(db_file))

    # Save a draft comment
    c_id = test_repo.save_linkedin_growth_comment(
        target_id=1,
        target_nome="Daniel Becker",
        post_url="https://www.linkedin.com/feed/update/urn:li:activity:77777/",
        post_texto="Discussão profunda sobre agentes autônomos em escritórios de advocacia.",
        post_autor="Daniel Becker",
        comentario_gerado="Comentário longo e prolixo inicial gerado pela IA...",
    )

    # 1. Edit comment manually
    new_text = "Ponto cirúrgico, Daniel. Aqui na prática vemos que sem dados limpos o agente não para de alucinar."
    ok_edit = test_repo.update_linkedin_growth_comment_text(c_id, new_text)
    assert ok_edit is True

    c_updated = test_repo.get_linkedin_growth_comment_by_id(c_id)
    assert c_updated["comentario_gerado"] == new_text

    # 2. Record to style memory
    ok_mem = test_repo.record_style_memory(c_id, new_text, instrucao="mais curto e direto")
    assert ok_mem is True

    # 3. Retrieve style examples
    examples = test_repo.get_style_examples(limit=5)
    assert len(examples) == 1
    assert examples[0]["comentario_final"] == new_text
    assert examples[0]["foi_editado"] == 1

    # 4. Refine with mocked Gemini
    mock_gemini = MagicMock()
    mock_gemini.generate_structured.return_value = SniperCommentResult(
        comentario="Cirúrgico, Daniel. Na bancada a gente vê que sem dados limpos o agente falha rápido.",
        tese_central="Agentes autônomos precisam de dados",
        angulo_utilizado="vivência prática",
    )
    refine_res = refine_sniper_comment(
        post_text="Discussão profunda sobre agentes autônomos...",
        current_comment=new_text,
        instruction="deixa mais curto",
        author_name="Daniel Becker",
        gemini_client=mock_gemini,
    )
    assert "Cirúrgico" in refine_res.comentario
    mock_gemini.generate_structured.assert_called_once()

