"""
Automated unit and contract tests for external integrations.
Covers EvolutionClient, TwentyCRMClient, GeminiClient schemas, and GoogleServices.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from integrations.evolution import EvolutionClient, format_brazilian_phone
from integrations.twenty import TwentyCRMClient
from integrations.gemini import LeadAuditResult, CuradoriaPautasResult, PautaEditorial, FonteNoticia
from integrations.google_service import GoogleServicesClient


# ==============================================================================
# 1. Phone Normalization Tests
# ==============================================================================
def test_format_brazilian_phone_standard():
    assert format_brazilian_phone("5519998256557") == "5519998256557"
    assert format_brazilian_phone("(19) 99825-6557") == "5519998256557"
    assert format_brazilian_phone("019998256557") == "5519998256557"


def test_format_brazilian_phone_omitted_ddd():
    # 9 digits -> prepends 19 + 55
    assert format_brazilian_phone("998256557") == "5519998256557"
    # 8 digits -> prepends 19 + 55
    assert format_brazilian_phone("38256557") == "551938256557"


def test_format_brazilian_phone_invalid():
    with pytest.raises(ValueError):
        format_brazilian_phone("")

    with pytest.raises(ValueError):
        format_brazilian_phone("abc")

    with pytest.raises(ValueError):
        format_brazilian_phone("123")  # too short


# ==============================================================================
# 2. Evolution API Client Tests
# ==============================================================================
@pytest.mark.anyio
async def test_evolution_check_whatsapp_number():
    client = EvolutionClient(base_url="http://test-evo:8080", api_key="secret", instance="TestInstance")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: [{"exists": True, "number": "5519998256557"}],
            raise_for_status=lambda: None,
        )

        exists = await client.check_whatsapp_number("19998256557")
        assert exists is True
        mock_post.assert_called_once()


@pytest.mark.anyio
async def test_evolution_send_text_message():
    client = EvolutionClient(base_url="http://test-evo:8080", api_key="secret", instance="TestInstance")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": "SUCCESS", "message": "Message queued"},
            raise_for_status=lambda: None,
        )

        res = await client.send_text_message("19998256557", "Olá!")
        assert res["status"] == "SUCCESS"


# ==============================================================================
# 3. Twenty CRM Client Tests
# ==============================================================================
@pytest.mark.anyio
async def test_twenty_find_person_by_email():
    client = TwentyCRMClient(base_url="http://test-crm:3000/rest", token="token123")

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": {"people": [{"id": "uuid-123", "name": {"firstName": "João"}}]}},
            raise_for_status=lambda: None,
        )

        person = await client.find_person_by_email("joao@example.com")
        assert person is not None
        assert person["id"] == "uuid-123"


@pytest.mark.anyio
async def test_twenty_create_person_and_note():
    client = TwentyCRMClient(base_url="http://test-crm:3000/rest", token="token123")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        # 1. Create person mock
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"data": {"id": "person-uuid-456"}},
            raise_for_status=lambda: None,
        )
        p_id = await client.create_person("Maria", "Silva", "maria@example.com", "19999999999")
        assert p_id == "person-uuid-456"

        # 2. Create note mock
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"data": {"id": "note-uuid-789"}},
            raise_for_status=lambda: None,
        )
        n_id = await client.create_note("Formulário Contato", "Mensagem de teste")
        assert n_id == "note-uuid-789"


# ==============================================================================
# 4. Gemini Schemas Tests
# ==============================================================================
def test_lead_audit_schema_validation():
    valid_data = {
        "aprovado": True,
        "status": "REAL",
        "score_confianca": 0.95,
        "acao_sugerida": "avancar",
        "motivo": "Lead legítimo com telefone de Campinas e e-mail corporativo.",
    }
    obj = LeadAuditResult.model_validate(valid_data)
    assert obj.aprovado is True
    assert obj.status == "REAL"
    assert obj.score_confianca == 0.95


def test_pautas_curadoria_schema_validation():
    pauta = PautaEditorial(
        id=1,
        titulo="Inteligência Artificial no Judiciário",
        angulo_editorial="Como o STF e CNJ estão regulando o uso de IA pelos tribunais",
        fontes_relacionadas=[
            FonteNoticia(id_noticia=1, veiculo="ConJur", data="15/09/2026", titulo_original="CNJ edita norma sobre IA")
        ],
        sintese_fiel_das_materias="O CNJ estabeleceu diretrizes para auditoria algorítmica.",
        topicos_para_redacao=["1. A nova resolução", "2. Impacto nos escritórios", "3. Conclusão"],
    )
    res = CuradoriaPautasResult(pautas=[pauta])
    assert len(res.pautas) == 1
    assert res.pautas[0].id == 1


# ==============================================================================
# 5. Google Services Mock Tests
# ==============================================================================
def test_google_services_calendar_mock():
    client = GoogleServicesClient(client_id="test_id", client_secret="test_secret")

    with patch.object(client, "get_calendar_credentials") as mock_creds, \
         patch("integrations.google_service.build") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_events = mock_service.events.return_value
        mock_events.list.return_value.execute.return_value = {"items": [{"id": "evt1"}]}

        events = client.list_calendar_events("cal_id", "2026-09-17T10:00:00Z", "2026-09-17T11:00:00Z")
        assert len(events) == 1
        assert events[0]["id"] == "evt1"
