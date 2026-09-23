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
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from api.schemas.enrichment import (
    CadastralData,
    CommercialIntelligence,
    CommercialStrategyData,
    CompanyEnrichmentRequest,
    CompanyEnrichmentResponse,
    DecisionMakerProfile,
    DecisionMakersQueryPlan,
    DecisionMakersRequest,
    DecisionMakersResponse,
    DigitalPresence,
    FullCompanyEnrichmentResponse,
    GoogleEnrichmentData,
    LinkedInCompanyProfile,
    LinkedInEnrichmentData,
    MarketProfile,
    QuickEnrichRequest,
    SearchRefinementPlan,
    UnifiedEnrichmentResponse,
)
import concurrent.futures
from core.celery_app import celery_app
from integrations.gemini import GeminiClient
from integrations.receita import (
    consult_cnpj_public_api,
    extract_cnpjs_from_search_results,
    format_cnpj,
)
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
Sua missão é gerar exatamente 3 consultas otimizadas e limpas para o Google Search com o objetivo de identificar:
1. O site oficial e apresentação institucional da empresa (ex: '{company_name} Brasil site oficial' ou '{company_name} sobre nós').
2. O CNPJ, Razão Social oficial e dados cadastrais públicos na Receita Federal / portais brasileiros (ex: '{company_name} Brasil CNPJ Razao Social').
3. O perfil corporativo (Company Page) no LinkedIn (ex: '{company_name} site:linkedin.com/company').

Diretrizes obrigatórias:
- Se a marca for internacional ou conhecida globalmente, use obrigatoriamente o termo 'Brasil' na consulta cadastral para encontrar a subsidiária/CNPJ brasileiro.
- Mantenha as consultas limpas e diretas: EVITE operadores booleanos complexos com parênteses aninhados (como '("CNPJ" OR "Razao Social")') para prevenir detecção anti-bot do Google.
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

LINKEDIN_COMPANY_SYSTEM_INSTRUCTION = """
Você é um analista sênior de inteligência corporativa e especialista em OSINT do LinkedIn.
Sua missão é extrair e estruturar o perfil institucional da empresa no LinkedIn (Company Page) a partir dos títulos, snippets e URLs indexadas pelo Google.

Diretrizes obrigatórias:
1. 'nome': O nome institucional da empresa como consta no título ou cabeçalho do LinkedIn.
2. 'url': A URL corporativa oficial da Company Page no LinkedIn (ex: https://br.linkedin.com/company/matera). Apenas páginas de empresa (/company/), ignore perfis individuais (/in/).
3. 'tagline': O slogan, tagline ou frase de impacto da empresa no LinkedIn.
4. 'sobre': Resumo institucional do que a empresa faz (seção 'Sobre' ou 'About').
5. 'setor': Setor de atuação segundo a taxonomia do LinkedIn (ex: Serviços e consultoria de TI, Software, Bancos, etc.).
6. 'faixa_funcionarios': Porte ou faixa de colaboradores no LinkedIn (ex: '501-1.000 funcionários', '1.001-5.000 funcionários', etc.).
7. 'total_seguidores': Número de seguidores no LinkedIn se presente nos snippets.
8. 'sede': Cidade/Estado da sede registrada no perfil.
9. 'ano_fundacao': Ano de fundação se mencionado.
10. 'tipo_empresa': Tipo (ex: 'Empresa privada', 'Sociedade Anônima', 'Capital Aberto').
11. 'especialidades': Lista de competências e áreas de especialidade mencionadas.
12. 'vagas_url': Link para a aba de empregos/vagas da empresa no LinkedIn (/jobs), se houver menção.
13. Não invente dados: deixe campos nulos se não houver evidência factual nos snippets.
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


def execute_google_search(query: str, num_results: int = 10) -> dict:
    """
    Executes Google search via Celery scraping worker (equipped with Xvfb, noVNC, and anti-bot profile)
    with resilient fallback to direct GoogleSearchScraper in ephemeral/isolated mode.
    """
    # 1. First attempt: Celery scraping worker with Xvfb
    try:
        from flows.tasks_google_search import task_google_search
        async_task = task_google_search.apply_async(
            kwargs={"query": query, "num_results": num_results},
            expires=40,
        )
        data = async_task.get(timeout=35)
        if data and isinstance(data, dict):
            logger.info("Celery google search succeeded for '%s' (results=%d)", query[:40], len(data.get("organic_results", [])))
            return data
    except Exception as e_celery:
        logger.warning("Celery search delegation failed or timed out for '%s' (%s). Trying direct scraper fallback...", query[:40], e_celery)

    # 2. Fallback: Direct Playwright GoogleSearchScraper in ephemeral mode
    try:
        with GoogleSearchScraper(ephemeral=True) as scraper:
            return scraper.search(query, num_results=num_results)
    except Exception as e_direct:
        logger.error("Direct google search failed for '%s': %s", query[:40], e_direct)
        return {"query": query, "total_results": 0, "organic_results": []}


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
            model_name="gemini-3.5-flash-lite",
        )
        logger.info("Refined search queries generated: Primary='%s', Fiscal='%s'", plan.primary_query, plan.fiscal_query)
    except Exception as e_plan:
        logger.warning("Query planner fallback used (%s)", e_plan)
        loc_clause = f" {request.location_hint}" if request.location_hint else ""
        br_clause = " Brasil" if "brasil" not in company_name.lower() else ""
        plan = SearchRefinementPlan(
            primary_query=f'{company_name}{loc_clause} site oficial',
            fiscal_query=f'{company_name}{br_clause} CNPJ Razao Social',
            social_query=f'{company_name} site:linkedin.com/company',
            assumptions_and_context="Busca padrão baseada no nome informado.",
        )

    # --------------------------------------------------------------------------
    # Stage 2: Public Google Search Extractions (Parallel Execution)
    # --------------------------------------------------------------------------
    collected_results = []
    seen_urls = set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_primary = executor.submit(execute_google_search, plan.primary_query, 6)
        f_fiscal = executor.submit(execute_google_search, plan.fiscal_query, 6)

        try:
            res_primary = f_primary.result()
            for item in res_primary.get("organic_results", []):
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    collected_results.append(item)
        except Exception as e_prim:
            logger.warning("Primary search failed: %s", e_prim)

        try:
            res_fiscal = f_fiscal.result()
            for item in res_fiscal.get("organic_results", []):
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    collected_results.append(item)
        except Exception as e_fisc:
            logger.warning("Fiscal search failed: %s", e_fisc)

    # --------------------------------------------------------------------------
    # Stage 2.5: Official Corporate Registry Resolution (Minha Receita / BrasilAPI)
    # --------------------------------------------------------------------------
    candidate_cnpjs = extract_cnpjs_from_search_results(collected_results)
    cnpj_registry_data = None
    for cand in candidate_cnpjs[:3]:
        cnpj_registry_data = consult_cnpj_public_api(cand)
        if cnpj_registry_data:
            break

    # If no candidate found in snippets, perform focused directory search
    if not cnpj_registry_data:
        try:
            br_term = " Brasil" if "brasil" not in company_name.lower() else ""
            res_dir = execute_google_search(f'{company_name}{br_term} CNPJ site:cnpj.biz', num_results=3)
            dir_results = res_dir.get("organic_results", [])
            for item in dir_results:
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    collected_results.append(item)
            for cand in extract_cnpjs_from_search_results(dir_results)[:3]:
                cnpj_registry_data = consult_cnpj_public_api(cand)
                if cnpj_registry_data:
                    break
        except Exception as e_dir:
            logger.warning("Directory CNPJ search fallback error: %s", e_dir)

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

    if cnpj_registry_data:
        qsa_summary = ", ".join([f"{s['nome']} ({s.get('cargo', 'Sócio')})" for s in cnpj_registry_data.get("qsa", [])])
        evidence_lines.append(
            f"=== REGISTRO OFICIAL DA RECEITA FEDERAL DO BRASIL (AUTORIDADE MÁXIMA) ===\n"
            f"CNPJ: {cnpj_registry_data['cnpj']}\n"
            f"Razão Social: {cnpj_registry_data['razao_social']}\n"
            f"Nome Fantasia: {cnpj_registry_data['nome_fantasia'] or company_name}\n"
            f"Situação Cadastral: {cnpj_registry_data['situacao_cadastral']}\n"
            f"Sede: {cnpj_registry_data['sede']}\n"
            f"CNAE: {cnpj_registry_data.get('cnae_fiscal_descricao')}\n"
            f"Quadro de Sócios e Administradores (QSA): {qsa_summary}\n"
            f"Fonte: {cnpj_registry_data.get('fonte')}\n"
        )
        sources_used.append("https://minhareceita.org")

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
            model_name="gemini-3.5-flash-lite",
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

    # Apply high-authority official registry data if verified
    if cnpj_registry_data:
        enriched_response.dados_cadastrais.razao_social = cnpj_registry_data["razao_social"]
        enriched_response.dados_cadastrais.cnpj = cnpj_registry_data["cnpj"]
        enriched_response.dados_cadastrais.situacao_cadastral = cnpj_registry_data["situacao_cadastral"]
        enriched_response.dados_cadastrais.sede_localizacao = cnpj_registry_data["sede"]
        enriched_response.dados_cadastrais.qsa = cnpj_registry_data.get("qsa", [])
        if not enriched_response.dados_cadastrais.nome_fantasia:
            enriched_response.dados_cadastrais.nome_fantasia = cnpj_registry_data["nome_fantasia"] or company_name
        enriched_response.inteligencia_comercial.nivel_confianca = "ALTA"

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
Você é um especialista em recrutamento executivo, vendas B2B e técnicas avançadas de busca (Google Dorks) no LinkedIn e imprensa de negócios.
Sua missão é gerar duas queries de busca no Google que encontrem perfis de decisores e líderes de alta gestão da empresa especificada:
1. 'query_c_level': Busca no LinkedIn focada em C-Level, Founders, Sócios, Co-Founders, Diretor Presidente, CEO, CTO, COO, CFO, VP, Country Manager.
   - Formato obrigatório: (site:br.linkedin.com/in/ OR site:linkedin.com/in/) "{company_name}" (CEO OR CTO OR COO OR CFO OR Founder OR Sócio OR VP OR "Diretor Presidente" OR "Country Manager")
2. 'query_directors_heads': Busca no LinkedIn focada em Diretores, Heads de Área e Gerentes de alto escalão (Vendas, Tecnologia, Operações, Comercial, RH, Finanças, CHRO).
   - Formato obrigatório: (site:br.linkedin.com/in/ OR site:linkedin.com/in/) "{company_name}" (Diretor OR Diretora OR "Head de" OR Gerente OR CHRO OR CMO)
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
   - "C-Level / Sócio-Fundador": CEO, CTO, CFO, COO, CMO, CPO, Founder, Sócio, Coproprietário, VP, Country Manager, Administrador Legal.
   - "Diretoria": Diretor, Diretora, Diretor Geral.
   - "Gerência / Head": Head, Gerente Sênior, Gerente Geral, Líder.
   - "Coordenação / Especialista": Coordenador, Especialista Principal.
4. Análise Estratégica de Contato ('analise_estrategica_contato'):
   - Escreva uma análise pragmática de 2-3 frases orientando o time comercial: qual dos decisores encontrados é o melhor ponto de entrada para uma reunião e qual dor de negócio deve ser usada na abertura.
5. Sócios/Administradores da Receita Federal (QSA) e Imprensa:
   - Se houver administradores identificados no QUADRO DE SÓCIOS E ADMINISTRADORES (QSA) da Receita Federal ou em notícias de imprensa de negócios (anúncio/nomeação de CEO/diretor), inclua-os obrigatoriamente como decisores C-Level / Sócio-Fundador com vinculo_atual_confirmado=true. Se não houver link direto do LinkedIn, deixe linkedin_url como null.
"""


def find_decision_makers_pipeline(
    request: DecisionMakersRequest,
    gemini_client: Optional[GeminiClient] = None,
    qsa_members: Optional[List[Dict[str, Any]]] = None,
) -> DecisionMakersResponse:
    """
    Discovers management and decision-making professionals on LinkedIn and official registry using Google Dorks, QSA, and AI synthesis.
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

    loc_suffix = " Brasil" if "brasil" not in company_name.lower() else ""

    try:
        plan: DecisionMakersQueryPlan = gemini.generate_structured(
            prompt=planner_prompt,
            system_instruction=DECISION_MAKERS_PLANNER_INSTRUCTION,
            response_model=DecisionMakersQueryPlan,
            model_name="gemini-3.5-flash-lite",
        )
        logger.info("Decision maker queries generated: C-Level='%s', Directors='%s'", plan.query_c_level, plan.query_directors_heads)
    except Exception as e_plan:
        logger.warning("Decision maker query planner fallback used (%s)", e_plan)
        plan = DecisionMakersQueryPlan(
            query_c_level=f'(site:br.linkedin.com/in/ OR site:linkedin.com/in/) "{company_name}"{loc_suffix} (CEO OR CTO OR COO OR CFO OR Founder OR Sócio OR VP OR "Diretor Presidente")',
            query_directors_heads=f'(site:br.linkedin.com/in/ OR site:linkedin.com/in/) "{company_name}"{loc_suffix} (Diretor OR Diretora OR "Head de" OR Gerente OR CHRO)',
        )

    query_news = f'"{company_name}"{loc_suffix} ("anuncia" OR "nomeia" OR "novo CEO" OR "novo diretor" OR "CHRO" OR "contrata")'

    # 2. Public Google Search execution in parallel (C-Level, Directors, and Press/News)
    collected_results = []
    seen_urls = set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        f_c = executor.submit(execute_google_search, plan.query_c_level, request.max_results)
        f_dh = executor.submit(execute_google_search, plan.query_directors_heads, request.max_results)
        f_news = executor.submit(execute_google_search, query_news, 4)

        try:
            res_c = f_c.result()
            for item in res_c.get("organic_results", []):
                u = item.get("url", "")
                if "linkedin.com/in/" in u and u not in seen_urls:
                    seen_urls.add(u)
                    collected_results.append(item)
        except Exception as e_c:
            logger.warning("C-Level search failed: %s", e_c)

        try:
            res_dh = f_dh.result()
            for item in res_dh.get("organic_results", []):
                u = item.get("url", "")
                if "linkedin.com/in/" in u and u not in seen_urls:
                    seen_urls.add(u)
                    collected_results.append(item)
        except Exception as e_dh:
            logger.warning("Directors/Heads search failed: %s", e_dh)

        try:
            res_news = f_news.result()
            for item in res_news.get("organic_results", []):
                u = item.get("url", "")
                if u not in seen_urls:
                    seen_urls.add(u)
                    collected_results.append(item)
        except Exception as e_news:
            logger.warning("News search failed: %s", e_news)

    # 3. AI synthesis of structured decision makers
    evidence_lines = []

    qsa_text = ""
    if not qsa_members:
        try:
            res_qsa = execute_google_search(f"{company_name} Brasil CNPJ", 4)
            found_cnpjs = extract_cnpjs_from_search_results(res_qsa.get("organic_results", []))
            for cand in found_cnpjs[:2]:
                reg = consult_cnpj_public_api(cand)
                if reg and reg.get("qsa"):
                    qsa_members = reg.get("qsa")
                    logger.info("Retrieved %d official QSA members from Receita Federal for '%s'", len(qsa_members), company_name)
                    break
        except Exception as e_qsa_lookup:
            logger.debug("Automatic QSA lookup error: %s", e_qsa_lookup)

    if qsa_members:
        qsa_lines = [
            f"- {q['nome']} | Cargo Oficial no QSA da Receita Federal: {q.get('cargo', 'Administrador')} | {q.get('tipo', 'Pessoa Física')}"
            for q in qsa_members
            if q.get("nome") and q.get("tipo") != "Pessoa Jurídica"
        ]
        if qsa_lines:
            qsa_text = "\n=== QUADRO DE SÓCIOS E ADMINISTRADORES OFICIAL (RECEITA FEDERAL DO BRASIL) ===\n" + "\n".join(qsa_lines)
            evidence_lines.append(qsa_text)

    for item in collected_results[: request.max_results + 6]:
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

    {qsa_text}

    === RESULTADOS DE PERFIS DO LINKEDIN E NOTÍCIAS DE NEGÓCIOS ENCONTRADOS ===
    {evidence_text}

    Analise cada resultado e construa a lista de decisores da empresa '{company_name}'.
    Inclua a recomendação estratégica de contato ('analise_estrategica_contato').
    """

    try:
        response: DecisionMakersResponse = gemini.generate_structured(
            prompt=synthesis_prompt,
            system_instruction=DECISION_MAKERS_SYNTHESIS_INSTRUCTION,
            response_model=DecisionMakersResponse,
            model_name="gemini-3.5-flash-lite",
        )
    except Exception as e_synth:
        logger.error("Decision makers synthesis failed: %s", e_synth)
        response = DecisionMakersResponse(
            status="PARTIAL",
            company_name=company_name,
            total_encontrados=len(collected_results),
            decisores=[],
            analise_estrategica_contato=f"Foram identificados {len(collected_results)} resultados. Recomenda-se abordagem direta aos perfis listados.",
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
        company_name=comp_res.dados_cadastrais.nome_fantasia or comp_res.nome_pesquisado,
        website_or_domain=comp_res.presenca_digital.website_oficial,
        max_results=10,
    )
    dm_res = find_decision_makers_pipeline(
        dm_request,
        gemini_client=gemini,
        qsa_members=comp_res.dados_cadastrais.qsa,
    )

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


def extract_linkedin_company_pipeline(
    company_name: str,
    known_linkedin_url: Optional[str] = None,
    location_hint: Optional[str] = None,
    gemini_client: Optional[GeminiClient] = None,
    scraper: Optional[GoogleSearchScraper] = None,
) -> LinkedInCompanyProfile:
    """
    Discovers and extracts structured corporate data from the public LinkedIn Company Page
    via Google indexing and snippets, without triggering any LinkedIn authwall or bot bans.
    """
    gemini = gemini_client or GeminiClient()
    company_clean = company_name.strip()

    loc_str = f" {location_hint}" if location_hint else ""
    query = f'site:linkedin.com/company/ "{company_clean}"{loc_str}'

    search_items = []

    if scraper:
        try:
            res = scraper.search(query, num_results=4)
            for item in res.get("organic_results", []):
                u = item.get("url", "")
                if "linkedin.com/company" in u:
                    search_items.append(item)
        except Exception as e:
            logger.warning("LinkedIn company search via provided scraper failed for '%s': %s", company_clean, e)
    else:
        try:
            res = execute_google_search(query, num_results=4)
            for item in res.get("organic_results", []):
                u = item.get("url", "")
                if "linkedin.com/company" in u:
                    search_items.append(item)
        except Exception as e_sc:
            logger.warning("Scraper session error for LinkedIn company: %s", e_sc)

    if not search_items:
        return LinkedInCompanyProfile(
            nome=company_clean,
            url=known_linkedin_url,
        )

    evidence_lines = []
    for item in search_items:
        evidence_lines.append(
            f"- Título: {item.get('title')} | URL: {item.get('url')}\n"
            f"  Snippet: {item.get('snippet')}"
        )
    evidence_text = "\n".join(evidence_lines)

    prompt = f"""
    EMPRESA PESQUISADA: "{company_clean}"
    URL PRÉVIA CONHECIDA: {known_linkedin_url or 'N/A'}
    LOCALIZAÇÃO / CONTEXTO: {location_hint or 'N/A'}

    === RESULTADOS INDEXADOS DO LINKEDIN NO GOOGLE (COMPANY PAGE) ===
    {evidence_text}

    Extraia com máxima fidelidade factual as informações da Company Page da empresa no LinkedIn para o modelo estruturado.
    """

    try:
        profile: LinkedInCompanyProfile = gemini.generate_structured(
            prompt=prompt,
            system_instruction=LINKEDIN_COMPANY_SYSTEM_INSTRUCTION,
            response_model=LinkedInCompanyProfile,
            model_name="gemini-3.5-flash-lite",
        )
        if known_linkedin_url and not profile.url:
            profile.url = known_linkedin_url
        if not profile.nome:
            profile.nome = company_clean
        return profile
    except Exception as e:
        logger.warning("Failed to synthesize LinkedIn company profile for '%s': %s", company_clean, e)
        first_url = search_items[0].get("url") if search_items else known_linkedin_url
        return LinkedInCompanyProfile(
            nome=company_clean,
            url=first_url or known_linkedin_url,
        )


@celery_app.task(name="flows.flow_company_enrichment.task_extract_linkedin_company", queue="flows")
def task_extract_linkedin_company(payload_dict: dict) -> dict:
    """
    Celery task for LinkedIn Company Page extraction.
    """
    name = payload_dict.get("name") or payload_dict.get("company_name", "")
    known_url = payload_dict.get("url") or payload_dict.get("linkedin_url")
    location = payload_dict.get("location") or payload_dict.get("location_hint")
    profile = extract_linkedin_company_pipeline(name, known_linkedin_url=known_url, location_hint=location)
    return profile.model_dump()


def enrich_unified_pipeline(
    request: QuickEnrichRequest,
    gemini_client: Optional[GeminiClient] = None,
) -> UnifiedEnrichmentResponse:
    """
    Executes a streamlined Google + LinkedIn company enrichment returning a clean consolidated profile.
    Extracts both company institutional data and key decision makers on LinkedIn.
    """
    start_time = time.time()
    gemini = gemini_client or GeminiClient()

    # 1 & 2. Run Company Profile Enrichment and LinkedIn Company Page extraction concurrently
    comp_req = CompanyEnrichmentRequest(
        company_name=request.name,
        location_hint=request.location,
        deep_scrape_website=request.deep_scrape,
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_comp = executor.submit(enrich_company_pipeline, comp_req, gemini)
        f_li = executor.submit(
            extract_linkedin_company_pipeline,
            company_name=request.name,
            known_linkedin_url=None,
            location_hint=request.location,
            gemini_client=gemini,
        )
        comp_res = f_comp.result()
        linkedin_company = f_li.result()

    if comp_res.presenca_digital.linkedin_url and not linkedin_company.url:
        linkedin_company.url = comp_res.presenca_digital.linkedin_url

    # 3. Discover decision makers using commercial brand name and official QSA
    target_company = request.name or comp_res.dados_cadastrais.nome_fantasia or comp_res.nome_pesquisado
    dm_req = DecisionMakersRequest(
        company_name=target_company,
        website_or_domain=comp_res.presenca_digital.website_oficial,
        max_results=10,
    )
    dm_res = find_decision_makers_pipeline(
        request=dm_req,
        gemini_client=gemini,
        qsa_members=comp_res.dados_cadastrais.qsa,
    )

    elapsed = round(time.time() - start_time, 2)

    return UnifiedEnrichmentResponse(
        status="SUCCESS",
        nome_pesquisado=request.name,
        google=GoogleEnrichmentData(
            razao_social=comp_res.dados_cadastrais.razao_social,
            nome_fantasia=comp_res.dados_cadastrais.nome_fantasia,
            cnpj=comp_res.dados_cadastrais.cnpj,
            situacao_cadastral=comp_res.dados_cadastrais.situacao_cadastral,
            sede=comp_res.dados_cadastrais.sede_localizacao,
            website_oficial=comp_res.presenca_digital.website_oficial,
            telefones=comp_res.presenca_digital.telefones,
            emails=comp_res.presenca_digital.emails,
            setor=comp_res.perfil_mercado.setor_atuacao,
            nicho=comp_res.perfil_mercado.subsetor_nicho,
            porte_estimado=comp_res.perfil_mercado.porte_estimado,
            o_que_faz=comp_res.perfil_mercado.descricao_negocio,
            produtos_servicos=comp_res.perfil_mercado.principais_produtos_ou_servicos,
            fontes_google=comp_res.inteligencia_comercial.fontes_consultadas,
        ),
        linkedin=LinkedInEnrichmentData(
            company_url=linkedin_company.url or comp_res.presenca_digital.linkedin_url,
            empresa=linkedin_company,
            total_decisores_encontrados=dm_res.total_encontrados,
            decisores=dm_res.decisores,
        ),
        inteligencia_comercial=CommercialStrategyData(
            dor_de_mercado_resolvida=comp_res.inteligencia_comercial.dor_de_mercado_resolvida,
            sugestao_pitch_vendas=comp_res.inteligencia_comercial.sugestao_pitch_vendas,
            melhor_ponto_de_contato=dm_res.analise_estrategica_contato,
            nivel_confianca=comp_res.inteligencia_comercial.nivel_confianca,
        ),
        execution_time_seconds=elapsed,
    )


@celery_app.task(name="flows.flow_company_enrichment.task_enrich_unified", queue="flows")
def task_enrich_unified(payload_dict: dict) -> dict:
    """
    Celery task for unified Google + LinkedIn company enrichment.
    """
    req = QuickEnrichRequest(**payload_dict)
    response = enrich_unified_pipeline(req)
    return response.model_dump()
