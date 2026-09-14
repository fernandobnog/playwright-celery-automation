"""
Base Playwright Scraper with persistent context, stealth injection,
and lifecycle management.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, Page, sync_playwright

from core.config import settings
from scrapers.stealth import (
    STEALTH_EVASION_SCRIPT,
    get_random_user_agent,
    get_random_viewport,
)

logger = logging.getLogger(__name__)


class BasePlaywrightScraper:
    """
    Manages a Chromium browser session with persistent user_data_dir,
    stealth evasions, and humanized parameters.
    """

    def __init__(
        self,
        user_data_dir: Optional[str] = None,
        headless: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ):
        self.user_data_dir = user_data_dir or settings.PLAYWRIGHT_USER_DATA_DIR
        self.headless = (
            headless if headless is not None else settings.PLAYWRIGHT_HEADLESS
        )
        self.timeout_ms = timeout_ms or settings.PLAYWRIGHT_TIMEOUT_MS

        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self.viewport = get_random_viewport()
        self.user_agent = get_random_user_agent()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def start(self) -> BrowserContext:
        """Initializes Playwright and launches a persistent browser context."""
        logger.info(
            "Launching Chromium persistent context. Data dir: %s, Headless: %s",
            self.user_data_dir,
            self.headless,
        )

        Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)

        self._playwright = sync_playwright().start()

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-size={self.viewport['width']},{self.viewport['height']}",
        ]

        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            viewport=self.viewport,
            user_agent=self.user_agent,
            args=launch_args,
            ignore_default_args=["--enable-automation"],
            accept_downloads=True,
        )

        # Set default timeout
        self._context.set_default_timeout(self.timeout_ms)

        # Inject stealth evasions on every new page/frame
        self._context.add_init_script(STEALTH_EVASION_SCRIPT)

        return self._context

    def new_page(self) -> Page:
        """Returns the first page or opens a new page in the context."""
        if not self._context:
            raise RuntimeError("Scraper browser context is not initialized.")

        pages = self._context.pages
        page = pages[0] if pages else self._context.new_page()
        page.set_default_timeout(self.timeout_ms)
        return page

    def save_screenshot(self, page: Page, prefix: str = "screenshot") -> str:
        """Saves a timestamped screenshot to the downloads directory for audit."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{prefix}_{timestamp}.png"
        file_path = settings.downloads_path / filename
        page.screenshot(path=str(file_path), full_page=True)
        logger.info("Screenshot captured and saved to: %s", file_path)
        return str(file_path)

    def close(self):
        """Closes browser context and stops Playwright engine cleanly."""
        if self._context:
            try:
                self._context.close()
            except Exception as e:
                logger.warning("Error closing browser context: %s", e)
            self._context = None

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception as e:
                logger.warning("Error stopping Playwright instance: %s", e)
            self._playwright = None
        logger.info("Browser session closed.")
