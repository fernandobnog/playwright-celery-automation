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
)
from flows.flow_company_enrichment import enrich_company_pipeline, task_enrich_company

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
