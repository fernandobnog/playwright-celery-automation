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
    FullCompanyEnrichmentResponse,
)
from flows.flow_company_enrichment import (
    enrich_company_pipeline,
    enrich_full_company_pipeline,
    find_decision_makers_pipeline,
    task_enrich_company,
    task_find_decision_makers,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/enrich", tags=["Lead Enrichment (AI + Search)"])


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
    name: str = Query(..., min_length=1, max_length=200, description="Nome da empresa a enriquecer"),
    location: Optional[str] = Query(None, description="Dica de localização (cidade ou estado)"),
    segment: Optional[str] = Query(None, description="Dica de segmento de atuação"),
    deep_scrape: bool = Query(True, description="Baixar conteúdo da página oficial identificada"),
):
    """
    Convenience GET endpoint to enrich a company name via query parameters.
    """
    payload = CompanyEnrichmentRequest(
        company_name=name,
        location_hint=location,
        segment_hint=segment,
        deep_scrape_website=deep_scrape,
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
    name: str = Query(..., min_length=1, max_length=200, description="Nome da empresa"),
    domain: Optional[str] = Query(None, description="Domínio oficial da empresa (ex: matera.com)"),
    max_results: int = Query(10, ge=1, le=25, description="Quantidade máxima de perfis a retornar"),
):
    """
    Convenience GET endpoint to discover decision makers via query string.
    """
    payload = DecisionMakersRequest(
        company_name=name,
        website_or_domain=domain,
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
