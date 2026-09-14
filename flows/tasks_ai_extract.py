"""
Celery asynchronous tasks for AI content extraction and batch crawling.
"""

import logging
from typing import Any, Dict, List, Optional

from celery import chord, group
from core.celery_app import celery_app
from flows.tasks_etl import task_dispatch_webhook
from scrapers.ai_extractor import extractor

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="flows.tasks_ai_extract.task_ai_extract_url",
    queue="scraping",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=120,
    max_retries=2,
)
def task_ai_extract_url(
    self,
    url: str,
    mode: str = "auto",
    format_type: str = "markdown",
    include_links: bool = True,
    include_images: bool = True,
    wait_for_selector: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Celery task that extracts and converts a webpage to AI Markdown.
    Can run in parallel across worker-1 and worker-2.
    """
    task_id = self.request.id
    logger.info("[%s] Async AI extraction starting for: %s", task_id, url)
    result = extractor.extract(
        url=url,
        mode=mode,
        format_type=format_type,
        include_links=include_links,
        include_images=include_images,
        wait_for_selector=wait_for_selector,
    )
    result["celery_task_id"] = task_id
    return result


@celery_app.task(
    name="flows.tasks_ai_extract.task_aggregate_batch_ai_extract",
    queue="flows",
)
def task_aggregate_batch_ai_extract(
    results: List[Dict[str, Any]],
    webhook_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Callback aggregating multiple AI extraction results from different workers.
    """
    total_tokens = sum(r.get("tokens_estimated", 0) for r in results)
    total_words = sum(r.get("word_count", 0) for r in results)

    summary = {
        "status": "COMPLETED",
        "total_urls_processed": len(results),
        "total_tokens_estimated": total_tokens,
        "total_words": total_words,
        "results": results,
    }

    if webhook_url:
        task_dispatch_webhook.delay(payload=summary, webhook_url=webhook_url)

    return summary


def trigger_batch_ai_extraction(
    urls: List[str],
    mode: str = "auto",
    webhook_url: Optional[str] = None,
):
    """
    Dispatches parallel Celery chord across workers.
    """
    header = [task_ai_extract_url.s(url=u, mode=mode) for u in urls]
    callback = task_aggregate_batch_ai_extract.s(webhook_url=webhook_url)
    return chord(header)(callback)
