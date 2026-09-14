"""
Example Concrete Scraper: Quotes Scraper.
Demonstrates humanized interactions, DOM parsing, anti-detection,
and screenshot capturing with Playwright.
"""

import logging
from typing import Any, Dict, List, Optional

from scrapers.base import BasePlaywrightScraper
from scrapers.humanizer import human_scroll, human_sleep

logger = logging.getLogger(__name__)


class QuotesScraper(BasePlaywrightScraper):
    """
    Scrapes quotes, authors, and tags with realistic human interaction.
    """

    def scrape_quotes(
        self,
        base_url: str = "https://quotes.toscrape.com",
        tag: Optional[str] = None,
        max_items: int = 10,
    ) -> Dict[str, Any]:
        """
        Navigates to the target page, optionally filters by tag,
        scrolls realistically, extracts quotes, and takes an audit screenshot.
        """
        page = self.new_page()
        target_url = f"{base_url}/tag/{tag}/" if tag else base_url

        logger.info("Navigating to target URL: %s", target_url)
        page.goto(target_url, wait_until="domcontentloaded")
        human_sleep(1.0, 2.0)

        # Humanized scroll down to load view and trigger lazy rendering
        human_scroll(page, steps=3, min_distance=200, max_distance=350)

        # Extract quote elements
        quote_elements = page.locator(".quote")
        count = quote_elements.count()
        logger.info("Found %d quote blocks on page.", count)

        items: List[Dict[str, Any]] = []
        limit = min(count, max_items)

        for i in range(limit):
            quote_el = quote_elements.nth(i)
            text = quote_el.locator(".text").inner_text().strip()
            author = quote_el.locator(".author").inner_text().strip()
            tags = quote_el.locator(".tag").all_inner_texts()

            items.append({
                "quote": text,
                "author": author,
                "tags": tags,
                "index": i + 1,
            })

        # Capture screenshot for visual verification & debugging
        screenshot_path = self.save_screenshot(page, prefix="quote_scrape")

        return {
            "source_url": target_url,
            "items_count": len(items),
            "items": items,
            "screenshot_path": screenshot_path,
        }
