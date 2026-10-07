"""
Unit tests for LinkedIn Connection Autopilot flow, ICP filtering, and repository lifecycle.
"""

from unittest.mock import MagicMock, patch
import pytest

from flows.flow_linkedin_autopilot import (
    generate_invite_note,
    is_icp_prospect,
    task_linkedin_autopilot_discovery,
    task_linkedin_autopilot_inviter,
)
from storage.repository import PipelineRepository


def test_is_icp_prospect():
    assert is_icp_prospect("Head of Legal Operations | LegalOps Lead") is True
    assert is_icp_prospect("Chief Technology Officer (CTO) | Software Architect") is True
    assert is_icp_prospect("Advogado Corporativo | Direito Digital e IA") is True
    assert is_icp_prospect("Sócia no Escritório X | Compliance e DPO") is True
    assert is_icp_prospect("Gerente de RH e Gestão de Pessoas") is True
    assert is_icp_prospect("Chef de Cozinha | Gastronomia Francesa") is False
    assert is_icp_prospect("Personal Trainer | Musculação e Crossfit") is False
    assert is_icp_prospect("") is True  # Empty headline allows inspection


def test_generate_invite_note():
    note = generate_invite_note(nome="Mariana Silva", headline="LegalOps Manager")
    assert "Mariana" in note
    assert len(note) <= 300
    assert "CTO" in note or "tecnologia" in note


def test_repository_autopilot_lifecycle(tmp_path):
    db_file = tmp_path / "test_autopilot.db"
    repo = PipelineRepository(db_path=str(db_file))

    # Save prospect
    prospect_id = repo.save_autopilot_prospect(
        nome="Dra. Juliana Mendes",
        headline="Diretora Jurídica | LegalOps & Governança",
        profile_url="https://www.linkedin.com/in/juliana-mendes-test/",
        source_target_nome="Daniel Becker",
        source_post_url="https://www.linkedin.com/posts/test-post-1/",
        nicho="TI_JURIDICO",
    )
    assert prospect_id is not None

    # Check pending
    pending = repo.get_pending_autopilot_prospects(limit=10)
    assert len(pending) == 1
    assert pending[0]["nome"] == "Dra. Juliana Mendes"
    assert pending[0]["status"] == "DISCOVERED"

    # Daily count before invite
    assert repo.get_daily_autopilot_invites_count() == 0

    # Update to INVITED
    updated = repo.update_autopilot_prospect_status(
        prospect_id=prospect_id,
        status="INVITED",
        invite_note="Olá Juliana, prazer em conectar!",
    )
    assert updated is True

    # Daily count after invite
    assert repo.get_daily_autopilot_invites_count() == 1

    # Check stats
    stats = repo.get_autopilot_stats()
    assert stats["total_discovered"] == 1
    assert stats["total_invited"] == 1
    assert stats["pending_queue"] == 0
    assert stats["today_invited"] == 1


@patch("flows.flow_linkedin_autopilot.linkedin_publisher")
@patch("flows.flow_linkedin_autopilot.repo")
def test_task_linkedin_autopilot_inviter_daily_limit(mock_repo, mock_pub):
    mock_repo.get_daily_autopilot_invites_count.return_value = 15

    res = task_linkedin_autopilot_inviter(batch_size=5, daily_limit=15)
    assert res["status"] == "DAILY_LIMIT_REACHED"
    assert res["sent_today"] == 15
    mock_pub.send_connection_invite.assert_not_called()
