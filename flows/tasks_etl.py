"""
ETL Tasks for Celery Canvas Workflows.
Demonstrates:
- Resilient scraping with Playwright
- Data normalization and enrichment in Python
- Persistence into SQLite and JSON storage
- Outbound Webhook dispatch with exponential backoff retry policies
- Error handling callback hooks
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from celery import Task
from core.celery_app import celery_app
from core.config import settings
from scrapers.quote_scraper import QuotesScraper
from storage.repository import repo

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="flows.tasks_etl.task_scrape_quotes",
    queue="scraping",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=180,
    retry_jitter=True,
    max_retries=3,
)
def task_scrape_quotes(
    self: Task,
    source_url: str = "https://quotes.toscrape.com",
    tag: Optional[str] = None,
    max_items: int = 10,
) -> Dict[str, Any]:
    """
    Step 1: Scrape target website using Playwright Chromium with persistent session.
    Retries automatically with exponential backoff on transient network failures.
    """
    task_id = self.request.id or "manual_task"
    logger.info("[%s] Starting scraping step from %s (tag=%s)", task_id, source_url, tag)
    
    repo.log_flow_start(task_id, "quote_etl_flow", {
        "source_url": source_url,
        "tag": tag,
        "max_items": max_items,
    })

    try:
        with QuotesScraper() as scraper:
            scraped_data = scraper.scrape_quotes(
                base_url=source_url,
                tag=tag,
                max_items=max_items,
            )

        scraped_data["task_id"] = task_id
        scraped_data["scraped_at"] = datetime.utcnow().isoformat()
        logger.info("[%s] Scraping step completed. Found %d items.", task_id, len(scraped_data.get("items", [])))
        return scraped_data

    except Exception as exc:
        logger.error("[%s] Scraping failed: %s. Attempting retry...", task_id, exc)
        raise exc


@celery_app.task(
    bind=True,
    name="flows.tasks_etl.task_transform_quotes",
    queue="flows",
)
def task_transform_quotes(self: Task, raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Step 2: Transform, clean, enrich, and compute analytics on the raw scraped payload.
    """
    task_id = self.request.id or raw_data.get("task_id", "unknown")
    logger.info("[%s] Starting data transformation and enrichment step", task_id)

    raw_items: List[Dict[str, Any]] = raw_data.get("items", [])
    transformed_items = []
    all_tags = set()
    total_words = 0

    for item in raw_items:
        quote = item.get("quote", "").strip()
        author = item.get("author", "").strip().title()
        tags = [t.strip().lower() for t in item.get("tags", [])]
        all_tags.update(tags)

        word_count = len(quote.split())
        total_words += word_count

        transformed_items.append({
            "quote": quote,
            "author": author,
            "tags": tags,
            "word_count": word_count,
            "char_count": len(quote),
            "is_short_quote": word_count < 15,
        })

    enriched_payload = {
        "task_id": task_id,
        "source_url": raw_data.get("source_url"),
        "screenshot_path": raw_data.get("screenshot_path"),
        "scraped_at": raw_data.get("scraped_at"),
        "transformed_at": datetime.utcnow().isoformat(),
        "total_items": len(transformed_items),
        "analytics": {
            "unique_tags_count": len(all_tags),
            "tags_list": sorted(list(all_tags)),
            "average_words_per_quote": (
                round(total_words / len(transformed_items), 2)
                if transformed_items else 0
            ),
        },
        "items": transformed_items,
    }

    logger.info("[%s] Transformation completed. %d items enriched.", task_id, len(transformed_items))
    return enriched_payload


@celery_app.task(
    bind=True,
    name="flows.tasks_etl.task_persist_quotes",
    queue="flows",
)
def task_persist_quotes(self: Task, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Step 3: Persist cleaned items into database and export to JSON in storage volume.
    """
    task_id = payload.get("task_id") or self.request.id
    logger.info("[%s] Starting persistence step", task_id)

    # 1. Save to SQLite database
    repo.save_scraped_items(
        task_id=task_id,
        source_url=payload.get("source_url", ""),
        items=payload.get("items", []),
    )

    # 2. Export structured JSON file into downloads
    export_filename = f"quotes_export_{task_id}.json"
    export_path = settings.downloads_path / export_filename
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    payload["export_file"] = str(export_path)
    repo.log_flow_complete(task_id, payload)

    logger.info("[%s] Persistence completed. Export saved to %s", task_id, export_path)
    return payload


@celery_app.task(
    bind=True,
    name="flows.tasks_etl.task_dispatch_webhook",
    queue="flows",
    autoretry_for=(httpx.HTTPError, httpx.TimeoutException, ConnectionError),
    retry_backoff=2,
    retry_jitter=True,
    max_retries=3,
)
def task_dispatch_webhook(
    self: Task,
    payload: Dict[str, Any],
    webhook_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Step 4: Dispatch payload to an external Webhook/API endpoint.
    Equipped with exponential backoff retry in case of transient 5xx or network errors.
    """
    task_id = payload.get("task_id") or self.request.id
    logger.info("[%s] Starting webhook dispatch step. Destination: %s", task_id, webhook_url)

    # If no external webhook URL was specified, simulate local mock dispatch
    target_url = webhook_url or f"http://{settings.API_HOST}:{settings.API_PORT}/api/v1/webhooks/incoming"

    summary_payload = {
        "event": "scraper.pipeline.completed",
        "task_id": task_id,
        "source_url": payload.get("source_url"),
        "total_items": payload.get("total_items"),
        "export_file": payload.get("export_file"),
        "screenshot_path": payload.get("screenshot_path"),
        "dispatched_at": datetime.utcnow().isoformat(),
        "summary": payload.get("analytics"),
    }

    try:
        with httpx.Client(timeout=settings.WEBHOOK_TIMEOUT) as client:
            response = client.post(target_url, json=summary_payload)
            response.raise_for_status()
            status_code = response.status_code
            response_text = response.text[:500]
            is_success = True

        repo.log_webhook_dispatch(
            task_id=task_id,
            webhook_url=target_url,
            status_code=status_code,
            response_body=response_text,
            is_success=is_success,
        )
        logger.info("[%s] Webhook successfully delivered to %s (Status %d)", task_id, target_url, status_code)

    except Exception as exc:
        logger.warning("[%s] Webhook delivery failed: %s. Retrying...", task_id, exc)
        repo.log_webhook_dispatch(
            task_id=task_id,
            webhook_url=target_url,
            status_code=0,
            response_body=str(exc),
            is_success=False,
        )
        raise exc

    return {
        "status": "COMPLETED",
        "task_id": task_id,
        "webhook_url": target_url,
        "webhook_status": status_code,
        "data": summary_payload,
    }


@celery_app.task(
    name="flows.tasks_etl.task_flow_failure_handler",
    queue="flows",
)
def task_flow_failure_handler(request, exc, traceback):
    """
    Error callback invoked automatically by Celery Canvas link_error
    when a pipeline stage permanently fails after exhausting retries.
    """
    task_id = request.id
    err_str = f"Workflow step failed with exception: {exc}"
    logger.error("[%s] ALERT: Pipeline failed permanently! Error: %s", task_id, err_str)
    repo.log_flow_error(task_id, err_str)


@celery_app.task(
    name="flows.tasks_etl.task_periodic_health_heartbeat",
    queue="default",
)
def task_periodic_health_heartbeat() -> Dict[str, str]:
    """Periodic Celery Beat task monitoring system heartbeat."""
    timestamp = datetime.utcnow().isoformat()
    logger.info("Celery Beat Heartbeat executed at %s", timestamp)
    return {"status": "HEALTHY", "timestamp": timestamp}
