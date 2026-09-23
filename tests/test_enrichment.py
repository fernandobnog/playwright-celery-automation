"""
Unit tests for Company & Lead Enrichment API and Pipeline.
Validates structured output parsing, query refinement, and mock search synthesis.
"""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.schemas.enrichment import (
    CadastralData,
    CommercialIntelligence,
    CompanyEnrichmentRequest,
    CompanyEnrichmentResponse,
    DigitalPresence,
    MarketProfile,
    SearchRefinementPlan,
)
from core.config import settings
from flows.flow_company_enrichment import enrich_company_pipeline

client = TestClient(app, headers={"X-API-Key": settings.INTERNAL_API_KEY or "omniflow_232750db9cac2682c20ffadd0bce268f2d85764bc1149921"})


def test_enrich_company_pipeline_mocked():
    """Validates the enrichment pipeline with mocked Google search and Gemini synthesis."""
    mock_gemini = MagicMock()

    # Stage 1: Query refinement plan
    mock_plan = SearchRefinementPlan(
        primary_query='"Matera" Campinas site oficial',
        fiscal_query='"Matera" Campinas CNPJ',
        social_query='"Matera" site:linkedin.com/company',
        assumptions_and_context="Empresa de tecnologia financeira em Campinas.",
    )

    # Stage 4: Synthesized company profile
    mock_response = CompanyEnrichmentResponse(
        status="SUCCESS",
        nome_pesquisado="Matera",
        dados_cadastrais=CadastralData(
            razao_social="Matera Informática S.A.",
            nome_fantasia="Matera",
            cnpj="58.749.123/0001-00",
            sede_localizacao="Campinas, SP",
        ),
        presenca_digital=DigitalPresence(
            website_oficial="https://www.matera.com",
            linkedin_url="https://www.linkedin.com/company/matera-systems",
            telefones=["(19) 3707-1200"],
            emails=["contato@matera.com"],
        ),
        perfil_mercado=MarketProfile(
            setor_atuacao="Tecnologia da Informação",
            subsetor_nicho="Core Banking & Pix",
            porte_estimado="Grande Empresa / Enterprise",
            descricao_negocio="Desenvolve soluções de tecnologia bancária e core banking.",
            principais_produtos_ou_servicos=["Core Banking", "Pix Instantâneo"],
            modelo_negocio="B2B / SaaS",
        ),
        inteligencia_comercial=CommercialIntelligence(
            dor_de_mercado_resolvida="Modernização de sistemas legados bancários.",
            sugestao_pitch_vendas="Focar em redução de custos operacionais com Pix.",
            nivel_confianca="ALTA",
            fontes_consultadas=["https://www.matera.com"],
        ),
    )

    mock_gemini.generate_structured.side_effect = [mock_plan, mock_response]

    mock_search_results = {
        "organic_results": [
            {
                "position": 1,
                "title": "Matera | Core Banking & Pix",
                "url": "https://www.matera.com",
                "display_url": "matera.com",
                "snippet": "Líder em soluções de tecnologia bancária e pagamentos no Brasil.",
            },
            {
                "position": 2,
                "title": "Matera Informática S.A. - CNPJ 58.749.123/0001-00",
                "url": "https://casadosdados.com.br/empresa/matera",
                "display_url": "casadosdados.com.br",
                "snippet": "CNPJ 58.749.123/0001-00 em Campinas/SP. Razão Social Matera Informática.",
            }
        ]
    }

    req = CompanyEnrichmentRequest(
        company_name="Matera",
        location_hint="Campinas SP",
        deep_scrape_website=False,
    )

    with patch("flows.flow_company_enrichment.GoogleSearchScraper") as MockScraper:
        mock_instance = MagicMock()
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.search.return_value = mock_search_results
        MockScraper.return_value = mock_instance

        result = enrich_company_pipeline(req, gemini_client=mock_gemini)

    assert result.status == "SUCCESS"
    assert result.nome_pesquisado == "Matera"
    assert result.dados_cadastrais.razao_social == "Matera Informática S.A."
    assert result.dados_cadastrais.cnpj == "58.749.123/0001-00"
    assert result.presenca_digital.website_oficial == "https://www.matera.com"
    assert result.inteligencia_comercial.nivel_confianca == "ALTA"
    assert result.execution_time_seconds is not None


def test_enrich_company_api_endpoints():
    """Tests POST and GET HTTP endpoints for company enrichment."""
    mock_enriched = CompanyEnrichmentResponse(
        status="SUCCESS",
        nome_pesquisado="Kavak",
        dados_cadastrais=CadastralData(
            razao_social="Kavak Tecnologia e Comércio de Veículos Ltda.",
            nome_fantasia="Kavak",
            cnpj="38.123.456/0001-99",
            sede_localizacao="São Paulo, SP",
        ),
        presenca_digital=DigitalPresence(
            website_oficial="https://www.kavak.com/br",
            linkedin_url="https://www.linkedin.com/company/kavak-brasil",
        ),
        perfil_mercado=MarketProfile(
            setor_atuacao="Automotivo / E-commerce",
            porte_estimado="Grande Empresa / Enterprise",
            descricao_negocio="Plataforma de compra e venda de veículos seminovos com garantia.",
        ),
        inteligencia_comercial=CommercialIntelligence(
            sugestao_pitch_vendas="Abordagem focada em soluções de financiamento ou vistoria.",
            nivel_confianca="ALTA",
            fontes_consultadas=["https://www.kavak.com/br"],
        ),
        execution_time_seconds=1.8,
    )

    with patch("api.routes.enrichment.enrich_company_pipeline", return_value=mock_enriched):
        # 1. Test POST endpoint
        resp_post = client.post(
            "/api/v1/enrich/company",
            json={"company_name": "Kavak", "location_hint": "São Paulo", "deep_scrape_website": False},
        )
        assert resp_post.status_code == 200
        data_post = resp_post.json()
        assert data_post["status"] == "SUCCESS"
        assert data_post["nome_pesquisado"] == "Kavak"
        assert data_post["dados_cadastrais"]["cnpj"] == "38.123.456/0001-99"
        assert data_post["presenca_digital"]["website_oficial"] == "https://www.kavak.com/br"

        # 2. Test GET endpoint
        resp_get = client.get(
            "/api/v1/enrich/company?name=Kavak&location=Sao+Paulo&deep_scrape=false"
        )
        assert resp_get.status_code == 200
        data_get = resp_get.json()
        assert data_get["status"] == "SUCCESS"
        assert data_get["perfil_mercado"]["setor_atuacao"] == "Automotivo / E-commerce"
