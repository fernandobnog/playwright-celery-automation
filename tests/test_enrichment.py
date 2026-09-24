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

    mock_cnpj_info = {
        "cnpj": "58.749.123/0001-00",
        "razao_social": "Matera Informática S.A.",
        "nome_fantasia": "Matera",
        "situacao_cadastral": "ATIVA",
        "sede": "Campinas, SP",
        "telefones": ["(19) 3707-1200"],
        "qsa": [{"nome": "Carlos Netto", "cargo": "Presidente"}],
        "fonte": "Receita Federal",
    }

    with patch("flows.flow_company_enrichment.execute_google_search", return_value=mock_search_results), \
         patch("flows.flow_company_enrichment.consult_cnpj_public_api", return_value=mock_cnpj_info), \
         patch("flows.flow_company_enrichment.verify_email_smtp", return_value=True):
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

    with patch("flows.flow_company_enrichment.execute_google_search", return_value=mock_search), \
         patch("flows.flow_company_enrichment.consult_cnpj_public_api", return_value=None), \
         patch("flows.flow_company_enrichment.verify_email_smtp", return_value=False), \
         patch("flows.flow_company_enrichment._safe_check_whatsapp", return_value=None):
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

    with patch("flows.flow_company_enrichment.execute_google_search", return_value=mock_search):
        result = extract_linkedin_company_pipeline("Matera", gemini_client=mock_gemini)

    assert result.nome == "Matera"
    assert result.url == "https://br.linkedin.com/company/matera"
    assert result.faixa_funcionarios == "501-1.000 funcionários"


def test_enrichment_schemas_with_people_emails_phones_and_aliases():
    """Validates that request schemas parse multiple people, emails, phones, contacts, and singular aliases."""
    from api.schemas.enrichment import ContactInput, DecisionMakersRequest, QuickEnrichRequest

    # 1. CompanyEnrichmentRequest with plural lists and structured contacts
    req1 = CompanyEnrichmentRequest(
        company_name="Matera",
        people=["Carlos Netto", "Roberto Matos"],
        emails=["carlos@matera.com", "contato@matera.com"],
        phones=["1937071200", "19999999999"],
        contacts=[{"nome": "Carlos Netto", "email": "carlos@matera.com", "telefone": "19999999999", "cargo": "CEO"}],
    )
    assert req1.company_name == "Matera"
    assert len(req1.people) == 2
    assert "carlos@matera.com" in req1.emails
    assert len(req1.phones) == 2
    assert len(req1.contacts) == 1
    assert req1.contacts[0].nome == "Carlos Netto"

    # 2. CompanyEnrichmentRequest with singular aliases and comma-separated strings
    req2 = CompanyEnrichmentRequest(
        name="Matera",
        person="Carlos Netto, Roberto Matos",
        email="carlos@matera.com, roberto@matera.com",
        phone="1937071200",
    )
    assert req2.company_name == "Matera"
    assert len(req2.people) == 2
    assert "Carlos Netto" in req2.people
    assert "Roberto Matos" in req2.people
    assert len(req2.emails) == 2
    assert len(req2.phones) == 1

    # 3. DecisionMakersRequest
    req3 = DecisionMakersRequest(
        company="Matera",
        people=["Carlos Netto"],
        email="carlos@matera.com",
        phones=["(19) 3707-1200"],
    )
    assert req3.company_name == "Matera"
    assert req3.people == ["Carlos Netto"]
    assert req3.emails == ["carlos@matera.com"]
    assert len(req3.phones) == 1

    # 4. QuickEnrichRequest
    req4 = QuickEnrichRequest(
        empresa="Matera",
        people="Carlos Netto, Roberto Matos",
        emails="carlos@matera.com",
        phones="19999999999",
    )
    assert req4.name == "Matera"
    assert len(req4.people) == 2
    assert len(req4.emails) == 1


def test_enrich_company_pipeline_with_contacts_audit():
    """Validates that enrich_company_pipeline audits provided emails and phones."""
    mock_gemini = MagicMock()
    mock_plan = SearchRefinementPlan(
        primary_query='"Matera" site oficial',
        fiscal_query='"Matera" CNPJ',
        social_query='"Matera" site:linkedin.com/company',
        assumptions_and_context="Empresa de software bancário.",
    )
    mock_response = CompanyEnrichmentResponse(
        status="SUCCESS",
        nome_pesquisado="Matera",
        dados_cadastrais=CadastralData(
            razao_social="Matera Informática S.A.",
            nome_fantasia="Matera",
            cnpj="58.749.123/0001-00",
        ),
        presenca_digital=DigitalPresence(
            website_oficial="https://www.matera.com",
            telefones=[],
            emails=[],
        ),
        perfil_mercado=MarketProfile(
            setor_atuacao="TI",
            porte_estimado="Grande",
            descricao_negocio="Core banking",
        ),
        inteligencia_comercial=CommercialIntelligence(
            sugestao_pitch_vendas="Pitch",
            nivel_confianca="ALTA",
        ),
    )
    mock_gemini.generate_structured.side_effect = [mock_plan, mock_response]

    req = CompanyEnrichmentRequest(
        company_name="Matera",
        emails=["contato@matera.com", "invalido@@"],
        phones=["1937071200"],
        deep_scrape_website=False,
    )

    with patch("flows.flow_company_enrichment.execute_google_search", return_value={"organic_results": []}), \
         patch("flows.flow_company_enrichment.consult_cnpj_public_api", return_value=None), \
         patch("flows.flow_company_enrichment.verify_email_smtp", return_value=True), \
         patch("flows.flow_company_enrichment._safe_check_whatsapp", return_value=True):
        result = enrich_company_pipeline(req, gemini_client=mock_gemini)

    assert result.status == "SUCCESS"
    assert result.auditoria_contatos is not None
    assert len(result.auditoria_contatos["emails_auditados"]) == 2
    valid_email = next(e for e in result.auditoria_contatos["emails_auditados"] if e["email"] == "contato@matera.com")
    assert valid_email["valido"] is True
    assert valid_email["status"] == "VALIDADO_SMTP"
    invalid_email = next(e for e in result.auditoria_contatos["emails_auditados"] if "invalido" in e["email"])
    assert invalid_email["valido"] is False
    assert invalid_email["status"] == "SINTAXE_INVALIDA"
    valid_phone = next(p for p in result.auditoria_contatos["telefones_auditados"] if p["telefone_original"] == "1937071200")
    assert valid_phone["valido"] is True
    assert valid_phone["whatsapp_ativo"] is True


def test_find_decision_makers_pipeline_with_target_people_and_qsa():
    """Validates find_decision_makers_pipeline cross-referencing target people with Receita Federal QSA."""
    from api.schemas.enrichment import DecisionMakerProfile, DecisionMakersQueryPlan, DecisionMakersRequest, DecisionMakersResponse
    from flows.flow_company_enrichment import find_decision_makers_pipeline

    mock_gemini = MagicMock()
    mock_plan = DecisionMakersQueryPlan(
        query_c_level='site:linkedin.com/in/ "Matera" CEO',
        query_directors_heads='site:linkedin.com/in/ "Matera" Diretor',
    )
    mock_resp = DecisionMakersResponse(
        status="SUCCESS",
        company_name="Matera",
        total_encontrados=1,
        decisores=[
            DecisionMakerProfile(
                nome="Carlos Netto",
                cargo="CEO & Founder",
                nivel_hierarquico="C-Level / Sócio-Fundador",
                departamento="Diretoria Geral",
                linkedin_url="https://br.linkedin.com/in/carlosnetto",
                vinculo_atual_confirmado=True,
            )
        ],
        analise_estrategica_contato="Abordar Carlos Netto.",
    )
    mock_gemini.generate_structured.side_effect = [mock_plan, mock_resp]

    mock_qsa_data = {
        "cnpj": "58.749.123/0001-00",
        "razao_social": "Matera Informática S.A.",
        "qsa": [
            {"nome": "Carlos Netto", "cargo": "Presidente / Sócio Administrador"},
            {"nome": "Roberto Matos", "cargo": "Diretor"},
        ],
    }

    req = DecisionMakersRequest(
        company_name="Matera",
        people=["Carlos Netto", "Novo Contato Injetado"],
        emails=["carlos@matera.com"],
        phones=["19999998888"],
    )

    with patch("flows.flow_company_enrichment.execute_google_search", return_value={"organic_results": []}), \
         patch("flows.flow_company_enrichment.find_valid_executive_email", return_value=None), \
         patch("flows.flow_company_enrichment.consult_cnpj_public_api", return_value=mock_qsa_data), \
         patch("flows.flow_company_enrichment.verify_email_smtp", return_value=True), \
         patch("flows.flow_company_enrichment._safe_check_whatsapp", return_value=True):
        result = find_decision_makers_pipeline(req, gemini_client=mock_gemini, qsa_members=mock_qsa_data["qsa"])

    assert result.status == "SUCCESS"
    assert result.total_encontrados >= 2
    carlos = next(d for d in result.decisores if "Carlos" in d.nome)
    assert carlos.pertence_ao_qsa is True
    assert carlos.cargo_qsa == "Presidente / Sócio Administrador"
    assert "carlos@matera.com" in carlos.emails_associados or carlos.email_provavel == "carlos@matera.com"
    assert carlos.whatsapp_valido is True

    injetado = next(d for d in result.decisores if "Novo Contato" in d.nome)
    assert injetado.origem_dado == "ENVIADO_PELO_USUARIO"


def test_unified_enrichment_api_with_contacts_payload():
    """Tests POST and GET /api/v1/enrich with lists of people, emails, and phones."""
    from api.schemas.enrichment import (
        CommercialStrategyData,
        ContactAuditSummary,
        DecisionMakerProfile,
        GoogleEnrichmentData,
        LinkedInCompanyProfile,
        LinkedInEnrichmentData,
        UnifiedEnrichmentResponse,
    )

    mock_unified = UnifiedEnrichmentResponse(
        status="SUCCESS",
        nome_pesquisado="Matera",
        google=GoogleEnrichmentData(
            razao_social="Matera Informática S.A.",
            nome_fantasia="Matera",
            cnpj="58.749.123/0001-00",
            sede="Campinas, SP",
            website_oficial="https://www.matera.com",
            telefones=["(19) 3707-1200"],
            emails=["contato@matera.com"],
            setor="Tecnologia",
            porte_estimado="Grande Empresa",
            o_que_faz="Desenvolve tecnologia bancária e core banking.",
        ),
        linkedin=LinkedInEnrichmentData(
            company_url="https://br.linkedin.com/company/matera",
            empresa=LinkedInCompanyProfile(
                nome="Matera",
                url="https://br.linkedin.com/company/matera",
            ),
            total_decisores_encontrados=1,
            decisores=[
                DecisionMakerProfile(
                    nome="Carlos Netto",
                    cargo="CEO",
                    nivel_hierarquico="C-Level / Sócio-Fundador",
                    departamento="Diretoria Geral",
                    linkedin_url="https://br.linkedin.com/in/carlosnetto",
                    pertence_ao_qsa=True,
                    cargo_qsa="Presidente",
                )
            ],
        ),
        contatos_enriquecidos_usuario=[
            DecisionMakerProfile(
                nome="Carlos Netto",
                cargo="CEO",
                nivel_hierarquico="C-Level / Sócio-Fundador",
                departamento="Diretoria Geral",
                linkedin_url="https://br.linkedin.com/in/carlosnetto",
                emails_associados=["carlos@matera.com"],
                telefones_associados=["(19) 3707-1200"],
                pertence_ao_qsa=True,
                cargo_qsa="Presidente",
                origem_dado="usuario_enriquecido",
            )
        ],
        auditoria_contatos=ContactAuditSummary(
            total_emails_fornecidos=1,
            emails_validos=["carlos@matera.com"],
            total_telefones_fornecidos=1,
            telefones_com_whatsapp=["(19) 3707-1200"],
        ),
        inteligencia_comercial=CommercialStrategyData(
            dor_de_mercado_resolvida="Modernização de legado e automação de Pix.",
            sugestao_pitch_vendas="Pitch estratégico.",
            melhor_ponto_de_contato="Carlos Netto",
            nivel_confianca="ALTA",
        ),
    )

    with patch("api.routes.enrichment.enrich_unified_pipeline", return_value=mock_unified):
        payload = {
            "name": "Matera",
            "people": ["Carlos Netto"],
            "emails": ["carlos@matera.com"],
            "phones": ["1937071200"],
            "contacts": [{"nome": "Carlos Netto", "email": "carlos@matera.com", "cargo": "CEO"}],
        }
        resp = client.post("/api/v1/enrich", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "SUCCESS"
        assert len(data["contatos_enriquecidos_usuario"]) == 1
        assert data["contatos_enriquecidos_usuario"][0]["pertence_ao_qsa"] is True
        assert data["auditoria_contatos"]["emails_validos"] == ["carlos@matera.com"]
        assert data["auditoria_contatos"]["telefones_com_whatsapp"] == ["(19) 3707-1200"]

        # Test GET with query parameters
        resp_get = client.get("/api/v1/enrich?name=Matera&people=Carlos+Netto&emails=carlos@matera.com&phones=1937071200")
        assert resp_get.status_code == 200
        assert resp_get.json()["status"] == "SUCCESS"



