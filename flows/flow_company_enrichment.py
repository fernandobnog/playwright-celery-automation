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
    DigitalPresence,
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
