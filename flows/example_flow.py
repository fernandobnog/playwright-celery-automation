"""
Pipeline Automation Flow: Quotes ETL.
Implements sequential chained execution using Celery Canvas (`chain`)
with automated retries, persistence, webhook notifications, and error callbacks.
Replaces no-code automation tools like n8n with native, typed Python workflows.
"""

import logging
from typing import Optional
from celery import chain
from celery.result import AsyncResult

from flows.tasks_etl import (
    task_scrape_quotes,
    task_transform_quotes,
    task_persist_quotes,
    task_dispatch_webhook,
    task_flow_failure_handler,
)

logger = logging.getLogger(__name__)


def build_quotes_pipeline(
    source_url: str = "https://quotes.toscrape.com",
    tag: Optional[str] = None,
    max_items: int = 10,
    webhook_url: Optional[str] = None,
):
    """
    Constructs a Celery Canvas chain:
    [Scrape with Playwright]
           │
           ▼
    [Transform & Enrich in Python]
           │
           ▼
    [Persist SQLite & JSON export]
           │
           ▼
    [Dispatch Webhook with Exponential Retry]
    """
    workflow = chain(
        task_scrape_quotes.s(
            source_url=source_url,
            tag=tag,
            max_items=max_items,
        ),
        task_transform_quotes.s(),
        task_persist_quotes.s(),
        task_dispatch_webhook.s(webhook_url=webhook_url),
    )
    return workflow


def trigger_quote_etl_flow(
    source_url: str = "https://quotes.toscrape.com",
    tag: Optional[str] = None,
    max_items: int = 10,
    webhook_url: Optional[str] = None,
) -> AsyncResult:
    """
    Executes the chained workflow asynchronously and attaches the global failure callback.
    Returns the AsyncResult handle of the lead task.
    """
    logger.info("Triggering Quote ETL Flow for %s (tag=%s)", source_url, tag)
    workflow = build_quotes_pipeline(
        source_url=source_url,
        tag=tag,
        max_items=max_items,
        webhook_url=webhook_url,
    )
    
    # Attach error handler callback for permanent pipeline failures
    result = workflow.apply_async(link_error=task_flow_failure_handler.s())
    return result


def trigger_scheduled_quote_etl(
    source_url: str = "https://quotes.toscrape.com",
    tag: Optional[str] = "inspirational",
    max_items: int = 5,
):
    """
    Entrypoint invoked periodically by Celery Beat scheduler.
    """
    logger.info("Executing periodic scheduled trigger for Quote ETL Flow")
    return trigger_quote_etl_flow(
        source_url=source_url,
        tag=tag,
        max_items=max_items,
    )
