"""
API Routes for triggering automation flows.
"""

from fastapi import APIRouter, HTTPException, Request
from api.schemas.requests import FlowTriggerResponse, ParallelETLRequest, QuoteETLRequest
from core.security import validate_url_for_ssrf
from flows.example_flow import trigger_quote_etl_flow
from flows.parallel_flow import trigger_parallel_crawl_flow

router = APIRouter(prefix="/flows", tags=["Automations / Flows"])


def get_vnc_links(request: Request):
    host = request.base_url.hostname or "localhost"
    return {
        "worker_1": f"http://{host}:6081/vnc.html?autoconnect=true",
        "worker_2": f"http://{host}:6082/vnc.html?autoconnect=true",
    }


@router.post("/quote-etl", response_model=FlowTriggerResponse)
def trigger_quote_flow(payload: QuoteETLRequest, request: Request):
    """
    Triggers the end-to-end chained automation pipeline:
    1. Scrape with Playwright on worker (visible on noVNC).
    2. Data cleaning & enrichment in Python.
    3. Storage in SQLite & JSON export.
    4. Outbound Webhook dispatch with exponential backoff retries.
    """
    try:
        validate_url_for_ssrf(payload.source_url, allow_internal_containers=False)
        if payload.webhook_url:
            validate_url_for_ssrf(payload.webhook_url, allow_internal_containers=True)

        async_result = trigger_quote_etl_flow(
            source_url=payload.source_url,
            tag=payload.tag,
            max_items=payload.max_items,
            webhook_url=payload.webhook_url,
        )

        return FlowTriggerResponse(
            task_id=async_result.id,
            flow_name="quote_etl_chained_pipeline",
            status="QUEUED",
            message="Workflow enqueued successfully. Watch browser execution on noVNC links.",
            status_url=f"/api/v1/tasks/{async_result.id}",
            vnc_urls=get_vnc_links(request),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to trigger flow: {e}")


@router.post("/parallel-etl", response_model=FlowTriggerResponse)
def trigger_parallel_flow(payload: ParallelETLRequest, request: Request):
    """
    Triggers a parallel workflow using Celery Canvas `chord`.
    Distributes page scraping across both worker-1 and worker-2 concurrently.
    """
    try:
        if payload.webhook_url:
            validate_url_for_ssrf(payload.webhook_url, allow_internal_containers=True)

        chord_result = trigger_parallel_crawl_flow(
            pages=payload.pages,
            webhook_url=payload.webhook_url,
        )

        return FlowTriggerResponse(
            task_id=chord_result.id,
            flow_name="parallel_chord_crawler",
            status="QUEUED",
            message="Parallel crawl chord dispatched across workers.",
            status_url=f"/api/v1/tasks/{chord_result.id}",
            vnc_urls=get_vnc_links(request),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to dispatch parallel flow: {e}")


@router.post("/whatsapp-eventos")
def trigger_whatsapp_eventos(max_messages: int = 5):
    """
    Triggers the venue re-engagement prospecting flow via WhatsApp.
    Identifies venues without upcoming gigs, validates phone numbers,
    and sends polite follow-up messages with anti-spam jitter.
    """
    from flows.flow_whatsapp_eventos import task_reengajar_locais_eventos
    task = task_reengajar_locais_eventos.delay(max_messages=max_messages)
    return {
        "status": "QUEUED",
        "task_id": task.id,
        "message": f"Whatsapp Eventos re-engagement task queued with max {max_messages} messages.",
        "status_url": f"/api/v1/tasks/{task.id}",
    }


@router.post("/editorial-pautas")
def trigger_editorial_pautas():
    """
    Triggers the daily editorial curation pipeline.
    Collects trending AI & Legal Tech news from Google News RSS,
    generates 2 structured editorial topics with Gemini AI, and dispatches via Gmail.
    """
    from flows.flow_editorial_pautas import task_daily_editorial_curation
    task = task_daily_editorial_curation.delay()
    return {
        "status": "QUEUED",
        "task_id": task.id,
        "message": "Daily editorial curation task queued in Celery.",
        "status_url": f"/api/v1/tasks/{task.id}",
    }

