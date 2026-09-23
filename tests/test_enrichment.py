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


def test_find_decision_makers_pipeline_mocked():
    """Validates the decision makers discovery pipeline with mocked Google search and Gemini."""
    from api.schemas.enrichment import (
        DecisionMakerProfile,
        DecisionMakersQueryPlan,
        DecisionMakersRequest,
        DecisionMakersResponse,
    )
    from flows.flow_company_enrichment import find_decision_makers_pipeline

    mock_gemini = MagicMock()

    mock_plan = DecisionMakersQueryPlan(
        query_c_level='site:linkedin.com/in/ "Matera" (CEO OR CTO OR Founder)',
        query_directors_heads='site:linkedin.com/in/ "Matera" (Diretor OR "Head de")',
    )

    mock_response = DecisionMakersResponse(
        status="SUCCESS",
        company_name="Matera",
        total_encontrados=2,
        decisores=[
            DecisionMakerProfile(
                nome="Carlos Netto",
                cargo="Co-founder e CEO",
                nivel_hierarquico="C-Level / Sócio-Fundador",
                departamento="Diretoria Geral",
                linkedin_url="https://br.linkedin.com/in/carlosnetto",
                localizacao="Campinas, SP",
                vinculo_atual_confirmado=True,
                resumo_experiencia="Co-fundador da Matera, liderando expansão de soluções bancárias.",
            ),
            DecisionMakerProfile(
                nome="Roberto Matos",
                cargo="Diretor de Tecnologia (CTO)",
                nivel_hierarquico="C-Level / Sócio-Fundador",
                departamento="Tecnologia",
                linkedin_url="https://br.linkedin.com/in/robertomatos",
                localizacao="Campinas, SP",
                vinculo_atual_confirmado=True,
                resumo_experiencia="Responsável pela arquitetura de sistemas Pix e core banking.",
            ),
        ],
        analise_estrategica_contato="Para propostas técnicas, abordar o CTO Roberto Matos; para parcerias corporativas, o CEO Carlos Netto.",
    )

    mock_gemini.generate_structured.side_effect = [mock_plan, mock_response]

    mock_search = {
        "organic_results": [
            {
                "title": "Carlos Netto - Co-founder e CEO - Matera | LinkedIn",
                "url": "https://br.linkedin.com/in/carlosnetto",
                "snippet": "Campinas, São Paulo, Brasil · Co-founder e CEO na Matera",
            },
            {
                "title": "Roberto Matos - Chief Technology Officer - Matera | LinkedIn",
                "url": "https://br.linkedin.com/in/robertomatos",
                "snippet": "Campinas, São Paulo, Brasil · CTO na Matera · Mais de 500 conexões",
            },
        ]
    }

    req = DecisionMakersRequest(company_name="Matera", max_results=5)

    with patch("flows.flow_company_enrichment.GoogleSearchScraper") as MockScraper:
        mock_inst = MagicMock()
        mock_inst.__enter__.return_value = mock_inst
        mock_inst.search.return_value = mock_search
        MockScraper.return_value = mock_inst

        result = find_decision_makers_pipeline(req, gemini_client=mock_gemini)

    assert result.status == "SUCCESS"
    assert result.company_name == "Matera"
    assert result.total_encontrados == 2
    assert result.decisores[0].nome == "Carlos Netto"
    assert result.decisores[0].nivel_hierarquico == "C-Level / Sócio-Fundador"
    assert "Roberto Matos" in result.analise_estrategica_contato


def test_decision_makers_api_endpoints():
    """Tests POST and GET endpoints for decision makers discovery."""
    from api.schemas.enrichment import (
        DecisionMakerProfile,
        DecisionMakersResponse,
        FullCompanyEnrichmentResponse,
    )

    mock_dm_resp = DecisionMakersResponse(
        status="SUCCESS",
        company_name="Totvs",
        total_encontrados=1,
        decisores=[
            DecisionMakerProfile(
                nome="Dennis Herszkowicz",
                cargo="Presidente e CEO",
                nivel_hierarquico="C-Level / Sócio-Fundador",
                departamento="Diretoria Geral",
                linkedin_url="https://br.linkedin.com/in/dennisherszkowicz",
                vinculo_atual_confirmado=True,
            )
        ],
        analise_estrategica_contato="Abordagem no nível de diretoria executiva.",
        execution_time_seconds=1.2,
    )

    with patch("api.routes.enrichment.find_decision_makers_pipeline", return_value=mock_dm_resp):
        # 1. POST /api/v1/enrich/decision-makers
        resp_post = client.post(
            "/api/v1/enrich/decision-makers",
            json={"company_name": "Totvs", "max_results": 5},
        )
        assert resp_post.status_code == 200
        data_post = resp_post.json()
        assert data_post["status"] == "SUCCESS"
        assert len(data_post["decisores"]) == 1
        assert data_post["decisores"][0]["nome"] == "Dennis Herszkowicz"

        # 2. GET /api/v1/enrich/decision-makers
        resp_get = client.get("/api/v1/enrich/decision-makers?name=Totvs&max_results=5")
        assert resp_get.status_code == 200
        data_get = resp_get.json()
        assert data_get["total_encontrados"] == 1


def test_unified_enrichment_api_endpoints():
    """Tests POST /api/v1/enrich and GET /api/v1/enrich returning Google and LinkedIn consolidated data."""
    from api.schemas.enrichment import (
        CommercialStrategyData,
        DecisionMakerProfile,
        GoogleEnrichmentData,
        LinkedInCompanyProfile,
        LinkedInEnrichmentData,
        UnifiedEnrichmentResponse,
    )

    mock_company_linkedin = LinkedInCompanyProfile(
        nome="Matera",
        url="https://br.linkedin.com/company/matera",
        tagline="Soluções em tecnologia para o mercado financeiro há 40 anos.",
        sobre="A Matera desenvolve soluções de tecnologia bancária, Pix e pagamentos.",
        setor="Serviços e consultoria de TI",
        faixa_funcionarios="501-1.000 funcionários",
        total_seguidores="45.000 seguidores",
        sede="Campinas, SP",
        ano_fundacao="1987",
        especialidades=["Core Banking", "Pix", "Open Finance"],
        vagas_url="https://br.linkedin.com/company/matera/jobs",
    )

    mock_unified = UnifiedEnrichmentResponse(
        status="SUCCESS",
        nome_pesquisado="Matera",
        google=GoogleEnrichmentData(
            razao_social="Matera Informática S.A.",
            nome_fantasia="Matera",
            cnpj="58.749.123/0001-00",
            situacao_cadastral="ATIVA",
            sede="Campinas, SP",
            website_oficial="https://www.matera.com",
            telefones=["(19) 3707-1200"],
            emails=["contato@matera.com"],
            setor="Tecnologia",
            nicho="Core Banking",
            porte_estimado="Grande Empresa / Enterprise",
            o_que_faz="Desenvolve tecnologia para o mercado financeiro e Pix.",
            produtos_servicos=["Core Banking", "Pix Instantâneo"],
            fontes_google=["https://www.matera.com"],
        ),
        linkedin=LinkedInEnrichmentData(
            company_url="https://br.linkedin.com/company/matera",
            empresa=mock_company_linkedin,
            total_decisores_encontrados=2,
            decisores=[
                DecisionMakerProfile(
                    nome="Carlos Netto",
                    cargo="Co-founder e CEO",
                    nivel_hierarquico="C-Level / Sócio-Fundador",
                    departamento="Diretoria Geral",
                    linkedin_url="https://br.linkedin.com/in/carlosnetto",
                    localizacao="Campinas, SP",
                    vinculo_atual_confirmado=True,
                ),
                DecisionMakerProfile(
                    nome="Roberto Matos",
                    cargo="CTO",
                    nivel_hierarquico="C-Level / Sócio-Fundador",
                    departamento="Tecnologia",
                    linkedin_url="https://br.linkedin.com/in/robertomatos",
                    localizacao="Campinas, SP",
                    vinculo_atual_confirmado=True,
                ),
            ],
        ),
        inteligencia_comercial=CommercialStrategyData(
            dor_de_mercado_resolvida="Modernização de legado e automação de Pix.",
            sugestao_pitch_vendas="Focar em ganhos de escala com processamento de pagamentos.",
            melhor_ponto_de_contato="Carlos Netto para estratégia ou Roberto Matos para tecnologia.",
            nivel_confianca="ALTA",
        ),
        execution_time_seconds=3.2,
    )

    with patch("api.routes.enrichment.enrich_unified_pipeline", return_value=mock_unified):
        # 1. POST with standard {"name": "Matera"}
        resp1 = client.post("/api/v1/enrich", json={"name": "Matera"})
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["status"] == "SUCCESS"
        assert data1["nome_pesquisado"] == "Matera"
        assert data1["google"]["cnpj"] == "58.749.123/0001-00"
        assert data1["google"]["website_oficial"] == "https://www.matera.com"
        assert data1["linkedin"]["empresa"]["nome"] == "Matera"
        assert data1["linkedin"]["empresa"]["faixa_funcionarios"] == "501-1.000 funcionários"
        assert len(data1["linkedin"]["decisores"]) == 2
        assert data1["linkedin"]["decisores"][0]["nome"] == "Carlos Netto"

        # 2. POST with alias {"company": "Matera"} or {"empresa": "Matera"}
        resp2 = client.post("/api/v1/enrich", json={"company": "Matera"})
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "SUCCESS"

        resp3 = client.post("/api/v1/enrich", json={"empresa": "Matera", "cidade": "Campinas"})
        assert resp3.status_code == 200
        assert resp3.json()["status"] == "SUCCESS"

        # 3. GET /api/v1/enrich?name=Matera
        resp_get = client.get("/api/v1/enrich?name=Matera&location=Campinas")
        assert resp_get.status_code == 200
        data_get = resp_get.json()
        assert data_get["status"] == "SUCCESS"
        assert data_get["google"]["razao_social"] == "Matera Informática S.A."
        assert data_get["linkedin"]["total_decisores_encontrados"] == 2
        assert data_get["linkedin"]["empresa"]["setor"] == "Serviços e consultoria de TI"


def test_linkedin_company_endpoint():
    """Tests POST and GET /api/v1/enrich/linkedin/company endpoints."""
    from api.schemas.enrichment import LinkedInCompanyProfile

    mock_profile = LinkedInCompanyProfile(
        nome="Nubank",
        url="https://br.linkedin.com/company/nubank",
        tagline="Reinvenção dos serviços financeiros na América Latina.",
        setor="Instituições financeiras",
        faixa_funcionarios="5.001-10.000 funcionários",
        total_seguidores="2.000.000+",
        sede="São Paulo, SP",
        ano_fundacao="2013",
        especialidades=["Fintech", "Cartão de Crédito", "Conta Digital"],
    )

    with patch("api.routes.enrichment.extract_linkedin_company_pipeline", return_value=mock_profile):
        # 1. POST /api/v1/enrich/linkedin/company
        resp_post = client.post("/api/v1/enrich/linkedin/company", json={"name": "Nubank"})
        assert resp_post.status_code == 200
        data_post = resp_post.json()
        assert data_post["nome"] == "Nubank"
        assert data_post["faixa_funcionarios"] == "5.001-10.000 funcionários"

        # 2. GET /api/v1/enrich/linkedin/company?name=Nubank
        resp_get = client.get("/api/v1/enrich/linkedin/company?name=Nubank")
        assert resp_get.status_code == 200
        data_get = resp_get.json()
        assert data_get["sede"] == "São Paulo, SP"


def test_extract_linkedin_company_pipeline_mocked():
    """Tests the extract_linkedin_company_pipeline with mocked Google search and Gemini structured response."""
    from api.schemas.enrichment import LinkedInCompanyProfile
    from flows.flow_company_enrichment import extract_linkedin_company_pipeline

    mock_gemini = MagicMock()
    expected_profile = LinkedInCompanyProfile(
        nome="Matera",
        url="https://br.linkedin.com/company/matera",
        tagline="A Matera faz tecnologia para o mercado financeiro há 40 anos.",
        sobre="Transformamos o negócio dos nossos clientes com soluções seguras e inovadoras.",
        setor="Serviços e consultoria de TI",
        faixa_funcionarios="501-1.000 funcionários",
        vagas_url="https://br.linkedin.com/company/matera/jobs",
    )
    mock_gemini.generate_structured.return_value = expected_profile

    mock_search = {
        "organic_results": [
            {
                "title": "Matera: Sobre | LinkedIn",
                "url": "https://br.linkedin.com/company/matera",
                "snippet": "A Matera faz tecnologia para o mercado financeiro há 40 anos, acompanhamos o Pix...",
            }
        ]
    }

    with patch("flows.flow_company_enrichment.GoogleSearchScraper") as MockScraper:
        mock_inst = MagicMock()
        mock_inst.__enter__.return_value = mock_inst
        mock_inst.search.return_value = mock_search
        MockScraper.return_value = mock_inst

        result = extract_linkedin_company_pipeline("Matera", gemini_client=mock_gemini)

    assert result.nome == "Matera"
    assert result.url == "https://br.linkedin.com/company/matera"
    assert result.faixa_funcionarios == "501-1.000 funcionários"


