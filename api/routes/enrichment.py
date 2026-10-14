"""
FastAPI Routes for AI-Powered Company & Lead Enrichment.
Exposes standalone endpoints to enrich company names into verified corporate profiles.
Zero CRM lock-in: results are returned pure and unpersisted directly to caller.
"""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from api.schemas.enrichment import (
    CompanyEnrichmentRequest,
    CompanyEnrichmentResponse,
    DecisionMakersRequest,
    DecisionMakersResponse,
    EmailVerifyRequest,
    EmailVerifyResponse,
    FullCompanyEnrichmentResponse,
    LinkedInCompanyProfile,
    QuickEnrichRequest,
    UnifiedEnrichmentResponse,
)
from integrations.email_verifier import verify_email_smtp
from flows.flow_company_enrichment import (
    enrich_company_pipeline,
    enrich_full_company_pipeline,
    enrich_unified_pipeline,
    extract_linkedin_company_pipeline,
    find_decision_makers_pipeline,
    task_enrich_company,
    task_enrich_unified,
    task_extract_linkedin_company,
    task_find_decision_makers,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/enrich", tags=["Lead Enrichment (AI + Search)"])


@router.post("", response_model=UnifiedEnrichmentResponse)
@router.post("/", response_model=UnifiedEnrichmentResponse)
def enrich_unified_post(payload: QuickEnrichRequest):
    """
    Endpoint principal: Envia apenas o nome da empresa e retorna os dados consolidados
    do Google (cadastral, site, o que faz) e do LinkedIn (página da empresa e decisores/gestores).
    """
    try:
        result = enrich_unified_pipeline(payload)
        return result
    except Exception as e:
        logger.error("Failed to execute unified enrichment for '%s': %s", payload.name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao enriquecer empresa via Google e LinkedIn: {str(e)}")


@router.get("", response_model=UnifiedEnrichmentResponse)
@router.get("/", response_model=UnifiedEnrichmentResponse)
def enrich_unified_get(
    name: Optional[str] = Query(None, min_length=1, max_length=200, description="Nome da empresa a enriquecer"),
    company_name: Optional[str] = Query(None, min_length=1, max_length=200, description="Alias para name"),
    location: Optional[str] = Query(None, description="Cidade ou Estado para desambiguação"),
    location_hint: Optional[str] = Query(None, description="Alias para location"),
    deep_scrape: Optional[bool] = Query(None, description="Análise profunda do site oficial"),
    deep_scrape_website: Optional[bool] = Query(None, description="Alias para deep_scrape"),
    people: Optional[str] = Query(None, description="Pessoas ou executivos separados por vírgula"),
    emails: Optional[str] = Query(None, description="E-mails corporativos ou pessoais separados por vírgula"),
    phones: Optional[str] = Query(None, description="Telefones separados por vírgula"),
):
    """
    Versão GET do enriquecimento unificado (Google + LinkedIn) via query string.
    Exemplo: /api/v1/enrich?name=Matera&location=Campinas&people=Carlos+Netto&emails=contato@matera.com
    """
    resolved_name = name or company_name
    if not resolved_name:
        raise HTTPException(status_code=422, detail="Parâmetro 'name' ou 'company_name' é obrigatório.")
    resolved_location = location or location_hint
    resolved_deep_scrape = deep_scrape if deep_scrape is not None else (deep_scrape_website if deep_scrape_website is not None else True)
    payload = QuickEnrichRequest(
        name=resolved_name,
        location=resolved_location,
        deep_scrape=resolved_deep_scrape,
        people=people,
        emails=emails,
        phones=phones,
    )
    return enrich_unified_post(payload)


@router.post("/company", response_model=CompanyEnrichmentResponse)
def enrich_company_post(payload: CompanyEnrichmentRequest):
    """
    Enriches a company name into a structured profile with cadastral data, digital presence,
    market positioning, and tactical commercial pitch suggestions using Gemini and Google Search.
    """
    try:
        result = enrich_company_pipeline(payload)
        return result
    except Exception as e:
        logger.error("Failed to enrich company '%s': %s", payload.company_name, e)
        raise HTTPException(status_code=500, detail=f"Erro ao enriquecer dados da empresa: {str(e)}")


@router.get("/company", response_model=CompanyEnrichmentResponse)
def enrich_company_get(
    name: Optional[str] = Query(None, min_length=1, max_length=200, description="Nome da empresa a enriquecer"),
    company_name: Optional[str] = Query(None, min_length=1, max_length=200, description="Alias para name"),
    location: Optional[str] = Query(None, description="Dica de localização (cidade ou estado)"),
    location_hint: Optional[str] = Query(None, description="Alias para location"),
    segment: Optional[str] = Query(None, description="Dica de segmento de atuação"),
    segment_hint: Optional[str] = Query(None, description="Alias para segment"),
    deep_scrape: Optional[bool] = Query(None, description="Baixar conteúdo da página oficial identificada"),
    deep_scrape_website: Optional[bool] = Query(None, description="Alias para deep_scrape"),
    people: Optional[str] = Query(None, description="Pessoas ou executivos separados por vírgula"),
    emails: Optional[str] = Query(None, description="E-mails corporativos separados por vírgula"),
    phones: Optional[str] = Query(None, description="Telefones separados por vírgula"),
):
    """
    Convenience GET endpoint to enrich a company name via query parameters.
    """
    resolved_name = name or company_name
    if not resolved_name:
        raise HTTPException(status_code=422, detail="Parâmetro 'name' ou 'company_name' é obrigatório.")
    resolved_location = location or location_hint
    resolved_segment = segment or segment_hint
    resolved_deep_scrape = deep_scrape if deep_scrape is not None else (deep_scrape_website if deep_scrape_website is not None else True)
    payload = CompanyEnrichmentRequest(
        company_name=resolved_name,
        location_hint=resolved_location,
        segment_hint=resolved_segment,
        deep_scrape_website=resolved_deep_scrape,
        people=people,
        emails=emails,
        phones=phones,
    )
    return enrich_company_post(payload)


@router.post("/company/async")
def enrich_company_async(payload: CompanyEnrichmentRequest):
    """
    Dispatches company enrichment as an asynchronous Celery task.
    Returns Celery task_id immediately for asynchronous polling or webhooks.
    """
    try:
        task = task_enrich_company.apply_async(args=[payload.model_dump()])
        return {
            "status": "QUEUED",
            "task_id": task.id,
            "company_name": payload.company_name,
            "message": "Enriquecimento enviado para a fila Celery com sucesso.",
        }
    except Exception as e:
        logger.error("Failed to dispatch async enrichment task: %s", e)
        raise HTTPException(status_code=500, detail=f"Erro ao enfileirar enriquecimento: {str(e)}")


@router.post("/decision-makers", response_model=DecisionMakersResponse)
def find_decision_makers_post(payload: DecisionMakersRequest):
    """
    Finds management and decision-making professionals (C-Level, Directors, VPs, Heads) on LinkedIn
    using Google Dorks and AI synthesis, without requiring a logged-in LinkedIn bot session.
    """
    try:
        result = find_decision_makers_pipeline(payload)
        return result
    except Exception as e:
        logger.error("Failed to find decision makers for '%s': %s", payload.company_name, e)
        raise HTTPException(status_code=500, detail=f"Erro ao buscar tomadores de decisão: {str(e)}")


@router.get("/decision-makers", response_model=DecisionMakersResponse)
def find_decision_makers_get(
    name: Optional[str] = Query(None, min_length=1, max_length=200, description="Nome da empresa"),
    company_name: Optional[str] = Query(None, min_length=1, max_length=200, description="Alias para name"),
    domain: Optional[str] = Query(None, description="Domínio oficial da empresa (ex: matera.com)"),
    website_or_domain: Optional[str] = Query(None, description="Alias para domain"),
    people: Optional[str] = Query(None, description="Nomes específicos de pessoas a pesquisar prioritariamente separados por vírgula"),
    target_people: Optional[str] = Query(None, description="Alias para people"),
    emails: Optional[str] = Query(None, description="E-mails corporativos conhecidos separados por vírgula"),
    provided_emails: Optional[str] = Query(None, description="Alias para emails"),
    phones: Optional[str] = Query(None, description="Telefones conhecidos separados por vírgula"),
    provided_phones: Optional[str] = Query(None, description="Alias para phones"),
    max_results: int = Query(10, ge=1, le=25, description="Quantidade máxima de perfis a retornar"),
):
    """
    Convenience GET endpoint to discover decision makers via query string.
    """
    resolved_name = name or company_name
    if not resolved_name:
        raise HTTPException(status_code=422, detail="Parâmetro 'name' ou 'company_name' é obrigatório.")
    resolved_domain = domain or website_or_domain
    raw_people = people or target_people
    raw_emails = emails or provided_emails
    raw_phones = phones or provided_phones
    target_peop = [p.strip() for p in raw_people.split(",") if p.strip()] if raw_people else []
    prov_emails = [e.strip() for e in raw_emails.split(",") if e.strip()] if raw_emails else []
    prov_phones = [ph.strip() for ph in raw_phones.split(",") if ph.strip()] if raw_phones else []
    payload = DecisionMakersRequest(
        company_name=resolved_name,
        website_or_domain=resolved_domain,
        target_people=target_peop,
        provided_emails=prov_emails,
        provided_phones=prov_phones,
        max_results=max_results,
    )
    return find_decision_makers_post(payload)


@router.post("/company/full", response_model=FullCompanyEnrichmentResponse)
def enrich_company_full_post(payload: CompanyEnrichmentRequest):
    """
    Unified 360° enrichment: Enriches cadastral, digital, and market data, then discovers
    key management and decision-making profiles on LinkedIn.
    """
    try:
        result = enrich_full_company_pipeline(payload)
        return result
    except Exception as e:
        logger.error("Failed to execute full 360 enrichment for '%s': %s", payload.company_name, e)
        raise HTTPException(status_code=500, detail=f"Erro ao executar enriquecimento 360: {str(e)}")


@router.post("/decision-makers/async")
def find_decision_makers_async(payload: DecisionMakersRequest):
    """
    Dispatches decision makers discovery as an asynchronous Celery task.
    """
    try:
        task = task_find_decision_makers.apply_async(args=[payload.model_dump()])
        return {
            "status": "QUEUED",
            "task_id": task.id,
            "company_name": payload.company_name,
            "message": "Busca de decisores enviada para a fila Celery com sucesso.",
        }
    except Exception as e:
        logger.error("Failed to dispatch async decision makers task: %s", e)
        raise HTTPException(status_code=500, detail=f"Erro ao enfileirar busca de decisores: {str(e)}")


@router.post("/linkedin/company", response_model=LinkedInCompanyProfile)
def extract_linkedin_company_post(payload: QuickEnrichRequest):
    """
    Retorna exclusivamente o perfil institucional e corporativo da empresa no LinkedIn (Company Page).
    """
    try:
        result = extract_linkedin_company_pipeline(
            company_name=payload.name,
            location_hint=payload.location,
        )
        return result
    except Exception as e:
        logger.error("Failed to extract LinkedIn company profile for '%s': %s", payload.name, e)
        raise HTTPException(status_code=500, detail=f"Erro ao extrair perfil corporativo do LinkedIn: {str(e)}")


@router.get("/linkedin/company", response_model=LinkedInCompanyProfile)
def extract_linkedin_company_get(
    name: str = Query(..., min_length=1, max_length=200, description="Nome da empresa"),
    location: Optional[str] = Query(None, description="Localização para desambiguação"),
):
    """
    Versão GET para extração do perfil corporativo da empresa no LinkedIn via query string.
    Exemplo: /api/v1/enrich/linkedin/company?name=Matera
    """
    payload = QuickEnrichRequest(name=name, location=location)
    return extract_linkedin_company_post(payload)


@router.post("/linkedin/company/async")
def extract_linkedin_company_async(payload: QuickEnrichRequest):
    """
    Enfileira a extração do perfil corporativo do LinkedIn na fila Celery assíncrona.
    """
    try:
        task = task_extract_linkedin_company.apply_async(args=[payload.model_dump()])
        return {
            "status": "QUEUED",
            "task_id": task.id,
            "company_name": payload.name,
            "message": "Extração de perfil corporativo do LinkedIn enviada para a fila Celery.",
        }
    except Exception as e:
        logger.error("Failed to dispatch async LinkedIn company task: %s", e)
        raise HTTPException(status_code=500, detail=f"Erro ao enfileirar extração do LinkedIn: {str(e)}")


@router.post("/verify-email", response_model=EmailVerifyResponse)
def verify_email_post(payload: EmailVerifyRequest):
    """
    Verificação ativa de entregabilidade de e-mail por handshake SMTP direto (zero-bounce).
    Testa se a caixa postal existe sem disparar mensagens de e-mail.
    """
    try:
        res = verify_email_smtp(payload.email)
        return EmailVerifyResponse(**res)
    except Exception as e:
        logger.error("Failed to verify email %s: %s", payload.email, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao verificar e-mail via SMTP: {str(e)}")


@router.get("/verify-email", response_model=EmailVerifyResponse)
def verify_email_get(email: str = Query(..., description="Endereço de e-mail corporativo a verificar")):
    """
    Versão GET para verificação ativa de e-mail por handshake SMTP.
    """
    payload = EmailVerifyRequest(email=email)
    return verify_email_post(payload)


