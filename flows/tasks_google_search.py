"""
Celery Task for Google Search Scraping.
Distributes Google Search execution to scraping workers with persistent Chromium profiles.
"""

import logging
from typing import Any, Dict

from core.celery_app import celery_app
from scrapers.google_scraper import GoogleSearchScraper

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="flows.tasks_google_search.task_google_search",
    queue="scraping",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=2,
)
def task_google_search(
    self,
    query: str,
    num_results: int = 10,
    lang: str = "pt-BR",
    country: str = "br",
    capture_screenshot: bool = False,
) -> Dict[str, Any]:
    """
    Executes a Google Search with full SERP extraction on a Celery worker.
    Viewable in real-time via noVNC stream on port 6081/6082.
    """
    task_id = self.request.id
    logger.info("[%s] Celery worker starting Google Search for: '%s'", task_id, query)

    with GoogleSearchScraper() as scraper:
        result = scraper.search(
            query=query,
            num_results=num_results,
            lang=lang,
            country=country,
            capture_screenshot=capture_screenshot,
        )

    result["celery_task_id"] = task_id
    return result
