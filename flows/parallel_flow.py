"""
Parallel Scraping Pipeline demonstrating Celery Canvas `group` and `chord`.
Distributes page scraping across worker-1 and worker-2 concurrently,
then aggregates results into a final callback.
"""

import logging
from typing import Any, Dict, List, Optional
from celery import chord, group
from core.celery_app import celery_app
from flows.tasks_etl import task_dispatch_webhook
from scrapers.quote_scraper import QuotesScraper

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="flows.parallel_flow.task_scrape_page",
    queue="scraping",
    max_retries=2,
)
def task_scrape_page(self, page_number: int) -> Dict[str, Any]:
    """
    Scrapes an individual page number.
    Can be picked up in parallel by worker-1 or worker-2.
    """
    url = f"https://quotes.toscrape.com/page/{page_number}/"
    logger.info("Parallel task scraping page %d at %s", page_number, url)
    
    with QuotesScraper() as scraper:
        data = scraper.scrape_quotes(base_url=url, max_items=5)
        data["page_number"] = page_number
        return data


@celery_app.task(
    name="flows.parallel_flow.task_aggregate_pages",
    queue="flows",
)
def task_aggregate_pages(results: List[Dict[str, Any]], webhook_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Chord callback: executed once all parallel page scraping tasks complete.
    Aggregates all scraped items from multiple workers.
    """
    total_items = []
    pages_processed = []

    for res in results:
        items = res.get("items", [])
        total_items.extend(items)
        pages_processed.append(res.get("page_number"))

    aggregated_payload = {
        "status": "AGGREGATED",
        "pages_processed": pages_processed,
        "total_items_count": len(total_items),
        "items": total_items,
    }

    logger.info("Aggregated %d items across pages %s", len(total_items), pages_processed)

    # Dispatch notification of aggregated batch
    task_dispatch_webhook.delay(payload=aggregated_payload, webhook_url=webhook_url)

    return aggregated_payload


def trigger_parallel_crawl_flow(pages: List[int], webhook_url: Optional[str] = None):
    """
    Dispatches parallel scrapers using Celery `chord`:
    Parallel group: [scrape_page(1), scrape_page(2), ...]
                           │
                           ▼
              [task_aggregate_pages]
    """
    logger.info("Dispatching parallel chord for pages: %s", pages)
    header = [task_scrape_page.s(page_num) for page_num in pages]
    callback = task_aggregate_pages.s(webhook_url=webhook_url)
    
    pipeline = chord(header)(callback)
    return pipeline
