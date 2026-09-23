"""
Flow: AI-Powered Company & Lead Enrichment Pipeline.
Transforms a bare company name into a comprehensive, verified corporate profile:
1. Gemini generates tactical, refined Google Search queries (official site, corporate registry/CNPJ, LinkedIn).
2. GoogleSearchScraper executes organic queries and captures public snippets.
3. ai_extractor extracts clean markdown content from the official website homepage/about page.
4. Gemini synthesizes evidence into strict cadastral, digital, market, and commercial intelligence schemas.
Zero CRM lock-in: Returns standalone enriched data directly to callers.
"""

import logging
import time
from typing import List, Optional
from urllib.parse import urlparse

from api.schemas.enrichment import (
    CadastralData,
    CommercialIntelligence,
    CompanyEnrichmentRequest,
    CompanyEnrichmentResponse,
    DecisionMakerProfile,
    DecisionMakersQueryPlan,
    DecisionMakersRequest,
    DecisionMakersResponse,
    DigitalPresence,
    FullCompanyEnrichmentResponse,
    MarketProfile,
    SearchRefinementPlan,
)
from core.celery_app import celery_app
from integrations.gemini import GeminiClient
from scrapers.ai_extractor import ai_extractor
from scrapers.google_scraper import GoogleSearchScraper

logger = logging.getLogger(__name__)

# Domains to exclude when searching for the candidate official corporate website
DIRECTORY_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "pinterest.com",
    "casadosdados.com.br", "econodata.com.br", "cnpj.biz", "consultacnpj.com",
    "cnpj.info", "situacaocadastral.info", "transparencia.cc", "consultas.procob.com",
    "reclameaqui.com.br", "glassdoor.com", "glassdoor.com.br", "infojobs.com.br",
    "jusbrasil.com.br", "wikipedia.org", "google.com", "bing.com", "yahoo.com"
}


QUERY_PLANNER_SYSTEM_INSTRUCTION = """
Você é um analista sênior de OSINT e inteligência corporativa B2B.
Sua missão é gerar exatamente 3 consultas otimizadas e específicas para o Google Search com o objetivo de identificar:
1. O site oficial e apresentação institucional da empresa.
2. O CNPJ, Razão Social oficial e dados cadastrais públicos na Receita Federal / portais brasileiros.
3. O perfil corporativo (Company Page) no LinkedIn.

Use operadores de busca (como aspas, OR, site:) quando oportuno para desambiguar e garantir relevância.
"""

SYNTHESIS_SYSTEM_INSTRUCTION = """
Você é um especialista em Inteligência de Mercado e Enriquecimento B2B de Leads.
Seu objetivo é analisar as evidências reais coletadas de pesquisas no Google e do site oficial da empresa para preencher rigorosamente o schema estruturado de enriquecimento.

Diretrizes obrigatórias:
1. Fidelidade Factual: NUNCA invente números de CNPJ, telefones ou e-mails que não estejam expressamente presentes nas evidências fornecidas. Se não encontrar, deixe como null ou lista vazia.
2. Se o CNPJ for encontrado, extraia e formate se possível (XX.XXX.XXX/XXXX-XX).
3. Síntese Comercial: No campo 'descricao_negocio', explique com objetividade o que a empresa vende e como gera receita.
4. Abordagem Consultiva: No campo 'sugestao_pitch_vendas', elabore uma sugestão prática de gancho para uma abordagem comercial.
5. Classificação de Confiança:
   - ALTA: Site oficial + CNPJ ou dados cadastrais confirmados.
   - MEDIA: Site oficial identificado com clareza, mas dados cadastrais incompletos.
   - BAIXA: Empresa ambígua ou poucos dados encontrados.
"""


def _is_official_domain_candidate(url: str) -> bool:
    """Checks whether URL belongs to an independent domain rather than an aggregator or social network."""
    if not url:
        return False
    try:
        domain = urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return not any(d in domain for d in DIRECTORY_DOMAINS)
    except Exception:
        return False


def enrich_company_pipeline(
    request: CompanyEnrichmentRequest,
    gemini_client: Optional[GeminiClient] = None,
) -> CompanyEnrichmentResponse:
    """
    Executes the multi-stage AI + Scrape enrichment pipeline.
    """
    start_time = time.time()
    company_name = request.company_name.strip()
    gemini = gemini_client or GeminiClient()

    logger.info("Starting enrichment pipeline for company: '%s'", company_name)

    # --------------------------------------------------------------------------
    # Stage 1: Query Refinement with Gemini
    # --------------------------------------------------------------------------
    planner_prompt = (
        f"Empresa: {company_name}\n"
        f"Dica de Localização: {request.location_hint or 'Não informada'}\n"
        f"Dica de Segmento/Nicho: {request.segment_hint or 'Não informada'}\n\n"
        "Gere as melhores queries para encontrar site oficial, CNPJ/Razão Social e LinkedIn."
    )

    try:
        plan: SearchRefinementPlan = gemini.generate_structured(
            prompt=planner_prompt,
            system_instruction=QUERY_PLANNER_SYSTEM_INSTRUCTION,
            response_model=SearchRefinementPlan,
            model_name="gemini-2.5-flash",
        )
        logger.info("Refined search queries generated: Primary='%s', Fiscal='%s'", plan.primary_query, plan.fiscal_query)
    except Exception as e_plan:
        logger.warning("Query planner fallback used (%s)", e_plan)
        loc_clause = f" {request.location_hint}" if request.location_hint else ""
        plan = SearchRefinementPlan(
            primary_query=f'"{company_name}"{loc_clause} site oficial',
            fiscal_query=f'"{company_name}"{loc_clause} CNPJ OR "Razao Social"',
            social_query=f'"{company_name}" site:linkedin.com/company',
            assumptions_and_context="Busca padrão baseada no nome informado.",
        )

    # --------------------------------------------------------------------------
    # Stage 2: Public Google Search Extractions
    # --------------------------------------------------------------------------
    collected_results = []
    seen_urls = set()

    with GoogleSearchScraper() as scraper:
        # 1. Primary organic search (site & institutional)
        try:
            res_primary = scraper.search(plan.primary_query, num_results=6)
            for item in res_primary.get("organic_results", []):
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    collected_results.append(item)
        except Exception as e_prim:
            logger.warning("Primary search failed: %s", e_prim)

        # 2. Fiscal search (CNPJ & registry data)
        try:
            res_fiscal = scraper.search(plan.fiscal_query, num_results=5)
            for item in res_fiscal.get("organic_results", []):
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    collected_results.append(item)
        except Exception as e_fisc:
            logger.warning("Fiscal search failed: %s", e_fisc)

    # --------------------------------------------------------------------------
    # Stage 3: Deep Scraping of Candidate Official Website (Optional)
    # --------------------------------------------------------------------------
    official_website_url = None
    for item in collected_results:
        url = item.get("url", "")
        if _is_official_domain_candidate(url):
            official_website_url = url
            break

    site_extracted_content = ""
    if request.deep_scrape_website and official_website_url:
        try:
            logger.info("Extracting clean page content from official domain: %s", official_website_url)
            page_data = ai_extractor.extract(official_website_url, max_length=4500, engine="fast")
            site_extracted_content = page_data.get("content", "")
        except Exception as e_site:
            logger.warning("Could not extract deep content from %s: %s", official_website_url, e_site)

    # --------------------------------------------------------------------------
    # Stage 4: AI Synthesis and Fact Consolidation
    # --------------------------------------------------------------------------
    evidence_lines = []
    sources_used = []
    for item in collected_results:
        sources_used.append(item["url"])
        evidence_lines.append(
            f"- [{item.get('position', '-')}] Título: {item.get('title')} | URL: {item.get('url')}\n"
            f"  Snippet: {item.get('snippet')}"
        )

    evidence_text = "\n".join(evidence_lines)

    synthesis_prompt = f"""
    ANALISE AS EVIDÊNCIAS COLETADAS PARA A EMPRESA: "{company_name}"
    Dica de Localização: {request.location_hint or 'Não informada'}
    Dica de Segmento: {request.segment_hint or 'Não informada'}

    === EVIDÊNCIAS DE BUSCA DO GOOGLE (SNIPPETS E RESULTADOS) ===
    {evidence_text}

    === CONTEÚDO EXTRAÍDO DO SITE OFICIAL ({official_website_url or 'N/A'}) ===
    {site_extracted_content[:4000] if site_extracted_content else 'Nenhum site oficial acessado diretamente.'}

    Preencha todos os campos do schema estruturado com máxima fidelidade factual às evidências.
    """

    class InternalSynthesisModel(CompanyEnrichmentResponse):
        pass

    try:
        enriched_response = gemini.generate_structured(
            prompt=synthesis_prompt,
            system_instruction=SYNTHESIS_SYSTEM_INSTRUCTION,
            response_model=CompanyEnrichmentResponse,
            model_name="gemini-2.5-flash",
        )
    except Exception as e_synth:
        logger.error("Synthesis failed: %s. Constructing baseline response.", e_synth)
        enriched_response = CompanyEnrichmentResponse(
            status="PARTIAL",
            nome_pesquisado=company_name,
            dados_cadastrais=CadastralData(
                razao_social=None,
                nome_fantasia=company_name,
                sede_localizacao=request.location_hint,
            ),
            presenca_digital=DigitalPresence(
                website_oficial=official_website_url,
            ),
            perfil_mercado=MarketProfile(
                setor_atuacao="Indefinido",
                porte_estimado="PME",
                descricao_negocio=f"Empresa identificada nas buscas sob o nome {company_name}.",
            ),
            inteligencia_comercial=CommercialIntelligence(
                sugestao_pitch_vendas=f"Validar área de atuação e principais dores de {company_name}.",
                nivel_confianca="BAIXA",
                fontes_consultadas=sources_used[:5],
            ),
        )

    # Attach metadata
    enriched_response.nome_pesquisado = company_name
    if not enriched_response.inteligencia_comercial.fontes_consultadas:
        enriched_response.inteligencia_comercial.fontes_consultadas = sources_used[:8]

    elapsed = round(time.time() - start_time, 2)
    enriched_response.execution_time_seconds = elapsed
    logger.info("Enrichment completed for '%s' in %.2f seconds", company_name, elapsed)

    return enriched_response


@celery_app.task(name="flows.flow_company_enrichment.task_enrich_company", queue="flows")
def task_enrich_company(payload_dict: dict) -> dict:
    """
    Celery task wrapper for asynchronous company enrichment.
    """
    req = CompanyEnrichmentRequest(**payload_dict)
    response = enrich_company_pipeline(req)
    return response.model_dump()


# ==============================================================================
# Pipeline 2: LinkedIn Decision Makers Discovery Pipeline
# ==============================================================================
DECISION_MAKERS_PLANNER_INSTRUCTION = """
Você é um especialista em recrutamento executivo, vendas B2B e técnicas avançadas de busca (Google Dorks) no LinkedIn.
Sua missão é gerar duas queries de busca no Google que encontrem perfis pessoais de decisores (LinkedIn /in/) da empresa especificada.

Regras obrigatórias para as queries:
1. Sempre use: site:linkedin.com/in/
2. Adicione o nome da empresa entre aspas: "{company_name}"
3. Na primeira query (query_c_level): foque em C-Level, Founders, Sócios, Co-Founders, Diretor Presidente, CEO, CTO, COO, CFO, VP.
4. Na segunda query (query_directors_heads): foque em Diretores, Heads de Área e Gerentes de alto escalão (Vendas, Tecnologia, Operações, Comercial).
"""

DECISION_MAKERS_SYNTHESIS_INSTRUCTION = """
Você é um especialista sênior em Sales Intelligence e mapeamento de organogramas corporativos.
Sua missão é analisar os títulos, URLs e snippets do Google Search de perfis do LinkedIn da empresa e estruturar a lista de tomadores de decisão reais.

Diretrizes obrigatórias:
1. Identificação do Nome e Cargo: Extraia o nome da pessoa e o cargo atual com fidelidade ao snippet/título do Google (formato "Nome - Cargo na Empresa | LinkedIn").
2. Confirmação do Vínculo Atual:
   - 'vinculo_atual_confirmado' deve ser TRUE se o snippet indicar que a pessoa atua atualmente na empresa.
   - Se o snippet indicar claramente que a pessoa já saiu da empresa (ex: "Ex-Diretor", "Anteriormente na ...", período finalizado no passado), marque FALSE ou descarte se não houver relevância.
3. Classificação Hierárquica:
   - "C-Level / Sócio-Fundador": CEO, CTO, CFO, COO, CMO, CPO, Founder, Sócio, Coproprietário, VP.
   - "Diretoria": Diretor, Diretora.
   - "Gerência / Head": Head, Gerente Sênior, Gerente Geral, Líder.
   - "Coordenação / Especialista": Coordenador, Especialista Principal.
4. Análise Estratégica de Contato ('analise_estrategica_contato'):
   - Escreva uma análise pragmática de 2-3 frases orientando o time comercial: qual dos decisores encontrados é o melhor ponto de entrada para uma reunião e qual dor de negócio deve ser usada na abertura.
"""


def find_decision_makers_pipeline(
    request: DecisionMakersRequest,
    gemini_client: Optional[GeminiClient] = None,
) -> DecisionMakersResponse:
    """
    Discovers management and decision-making professionals on LinkedIn using Google Dorks and AI synthesis.
    """
    start_time = time.time()
    company_name = request.company_name.strip()
    gemini = gemini_client or GeminiClient()

    logger.info("Starting decision makers discovery for company: '%s'", company_name)

    # 1. Query planning with Gemini
    planner_prompt = (
        f"Empresa: {company_name}\n"
        f"Domínio/Website: {request.website_or_domain or 'Não informado'}\n"
        f"Departamentos prioritários: {', '.join(request.target_departments or [])}\n"
        f"Senioridades desejadas: {', '.join(request.target_seniorities or [])}\n\n"
        "Gere as 2 queries otimizadas do Google Dork para perfis no LinkedIn."
    )

    try:
        plan: DecisionMakersQueryPlan = gemini.generate_structured(
            prompt=planner_prompt,
            system_instruction=DECISION_MAKERS_PLANNER_INSTRUCTION,
            response_model=DecisionMakersQueryPlan,
            model_name="gemini-2.5-flash",
        )
        logger.info("Decision maker queries generated: C-Level='%s', Directors='%s'", plan.query_c_level, plan.query_directors_heads)
    except Exception as e_plan:
        logger.warning("Decision maker query planner fallback used (%s)", e_plan)
        plan = DecisionMakersQueryPlan(
            query_c_level=f'site:linkedin.com/in/ "{company_name}" (CEO OR CTO OR COO OR CFO OR Founder OR Sócio OR VP)',
            query_directors_heads=f'site:linkedin.com/in/ "{company_name}" (Diretor OR Diretora OR "Head de" OR Gerente)',
        )

    # 2. Public Google Search execution
    collected_results = []
    seen_urls = set()

    with GoogleSearchScraper() as scraper:
        # Search 1: C-Level & Executives
        try:
            res_c = scraper.search(plan.query_c_level, num_results=request.max_results)
            for item in res_c.get("organic_results", []):
                u = item.get("url", "")
                if "linkedin.com/in/" in u and u not in seen_urls:
                    seen_urls.add(u)
                    collected_results.append(item)
        except Exception as e_c:
            logger.warning("C-Level search failed: %s", e_c)

        # Search 2: Directors & Heads
        if len(collected_results) < request.max_results:
            try:
                res_dh = scraper.search(plan.query_directors_heads, num_results=request.max_results)
                for item in res_dh.get("organic_results", []):
                    u = item.get("url", "")
                    if "linkedin.com/in/" in u and u not in seen_urls:
                        seen_urls.add(u)
                        collected_results.append(item)
            except Exception as e_dh:
                logger.warning("Directors/Heads search failed: %s", e_dh)

    # 3. AI synthesis of structured decision makers
    evidence_lines = []
    for item in collected_results[: request.max_results + 5]:
        evidence_lines.append(
            f"- Título: {item.get('title')}\n"
            f"  URL: {item.get('url')}\n"
            f"  Snippet: {item.get('snippet')}\n"
        )

    evidence_text = "\n".join(evidence_lines) if evidence_lines else "Nenhum resultado de perfil encontrado."

    synthesis_prompt = f"""
    EMPRESA ALVO: "{company_name}"
    Domínio/Website: {request.website_or_domain or 'N/A'}
    Departamentos de interesse: {', '.join(request.target_departments or [])}

    === RESULTADOS DE PERFIS DO LINKEDIN ENCONTRADOS VIA GOOGLE SEARCH ===
    {evidence_text}

    Analise cada resultado e construa a lista de decisores da empresa '{company_name}'.
    Inclua a recomendação estratégica de contato ('analise_estrategica_contato').
    """

    try:
        response: DecisionMakersResponse = gemini.generate_structured(
            prompt=synthesis_prompt,
            system_instruction=DECISION_MAKERS_SYNTHESIS_INSTRUCTION,
            response_model=DecisionMakersResponse,
            model_name="gemini-2.5-flash",
        )
    except Exception as e_synth:
        logger.error("Decision makers synthesis failed: %s", e_synth)
        response = DecisionMakersResponse(
            status="PARTIAL",
            company_name=company_name,
            total_encontrados=len(collected_results),
            decisores=[],
            analise_estrategica_contato=f"Foram identificados {len(collected_results)} links brutos no LinkedIn. Recomenda-se abordagem direta aos perfis listados.",
        )

    response.company_name = company_name
    response.total_encontrados = len(response.decisores)
    elapsed = round(time.time() - start_time, 2)
    response.execution_time_seconds = elapsed
    logger.info("Found %d decision makers for '%s' in %.2f seconds", response.total_encontrados, company_name, elapsed)

    return response


def enrich_full_company_pipeline(
    request: CompanyEnrichmentRequest,
    gemini_client: Optional[GeminiClient] = None,
) -> FullCompanyEnrichmentResponse:
    """
    Unified 360° pipeline: Enriches company cadastral & market data, then discovers decision makers.
    """
    start_time = time.time()
    gemini = gemini_client or GeminiClient()

    # 1. Company enrichment
    comp_res = enrich_company_pipeline(request, gemini_client=gemini)

    # 2. Decision makers discovery using enriched company signals
    dm_request = DecisionMakersRequest(
        company_name=comp_res.dados_cadastrais.razao_social or comp_res.nome_pesquisado,
        website_or_domain=comp_res.presenca_digital.website_oficial,
        max_results=10,
    )
    dm_res = find_decision_makers_pipeline(dm_request, gemini_client=gemini)

    elapsed = round(time.time() - start_time, 2)
    return FullCompanyEnrichmentResponse(
        status="SUCCESS",
        empresa=comp_res,
        decisores=dm_res,
        execution_time_seconds=elapsed,
    )


@celery_app.task(name="flows.flow_company_enrichment.task_find_decision_makers", queue="flows")
def task_find_decision_makers(payload_dict: dict) -> dict:
    """
    Celery task for asynchronous decision makers discovery.
    """
    req = DecisionMakersRequest(**payload_dict)
    response = find_decision_makers_pipeline(req)
    return response.model_dump()


@celery_app.task(name="flows.flow_company_enrichment.task_enrich_full_company", queue="flows")
def task_enrich_full_company(payload_dict: dict) -> dict:
    """
    Celery task for unified 360° company + decision makers enrichment.
    """
    req = CompanyEnrichmentRequest(**payload_dict)
    response = enrich_full_company_pipeline(req)
    return response.model_dump()
