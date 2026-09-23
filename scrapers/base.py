"""
Base Playwright Scraper with persistent context, stealth injection,
and lifecycle management.
"""

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from playwright.sync_api import BrowserContext, Page, sync_playwright

from core.config import settings
from scrapers.stealth import (
    STEALTH_EVASION_SCRIPT,
    get_random_user_agent,
    get_random_viewport,
)

logger = logging.getLogger(__name__)


def clean_chromium_locks(user_data_dir: str) -> None:
    """Removes stale Chromium POSIX singleton locks to prevent process lock crashes."""
    if not user_data_dir or not Path(user_data_dir).exists():
        return
    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_file = Path(user_data_dir) / lock_name
        try:
            if lock_file.is_symlink() or lock_file.exists():
                lock_file.unlink(missing_ok=True)
                logger.info("Cleaned stale Chromium lock: %s", lock_file)
        except Exception as e:
            logger.warning("Could not clean Chromium lock %s: %s", lock_file, e)


class BasePlaywrightScraper:
    """
    Manages a Chromium browser session with persistent user_data_dir or ephemeral profiles,
    stealth evasions, lock recovery, and humanized parameters.
    """

    def __init__(
        self,
        user_data_dir: Optional[str] = None,
        headless: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
        ephemeral: bool = False,
    ):
        self.ephemeral = ephemeral
        self._temp_dir: Optional[str] = None
        self.user_data_dir = user_data_dir or settings.PLAYWRIGHT_USER_DATA_DIR

        # Determine headless mode: if headless is requested as False but DISPLAY is not available, force True
        requested_headless = headless if headless is not None else settings.PLAYWRIGHT_HEADLESS
        if not requested_headless and not os.environ.get("DISPLAY"):
            logger.info("No DISPLAY available in environment; forcing Playwright headless=True")
            self.headless = True
        else:
            self.headless = requested_headless

        self.timeout_ms = timeout_ms or settings.PLAYWRIGHT_TIMEOUT_MS

        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self.viewport, self.user_agent = self._load_or_create_identity()

    def _load_or_create_identity(self) -> Tuple[dict, str]:
        """
        Maintains persistent persona fingerprinting (User-Agent and Viewport)
        for this profile directory.
        """
        if self.user_data_dir:
            meta_file = Path(self.user_data_dir) / "profile_identity.json"
            if meta_file.exists():
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if "viewport" in data and "user_agent" in data:
                            return data["viewport"], data["user_agent"]
                except Exception as e:
                    logger.warning("Could not read profile_identity.json: %s", e)

        viewport = get_random_viewport()
        user_agent = get_random_user_agent()
        if self.user_data_dir and not self.ephemeral:
            try:
                Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)
                meta_file = Path(self.user_data_dir) / "profile_identity.json"
                with open(meta_file, "w", encoding="utf-8") as f:
                    json.dump({"viewport": viewport, "user_agent": user_agent}, f, indent=2)
            except Exception as e:
                logger.warning("Could not save profile_identity.json: %s", e)

        return viewport, user_agent

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def start(self) -> BrowserContext:
        """Initializes Playwright and launches a persistent or ephemeral browser context."""
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

        target_dir = self.user_data_dir
        if self.ephemeral:
            self._temp_dir = tempfile.mkdtemp(prefix="playwright_ephemeral_")
            target_dir = self._temp_dir
        else:
            Path(target_dir).mkdir(parents=True, exist_ok=True)
            clean_chromium_locks(target_dir)

        logger.info(
            "Launching Chromium context. Data dir: %s (ephemeral=%s), Headless: %s, User-Agent: %s",
            target_dir,
            self.ephemeral,
            self.headless,
            self.user_agent,
        )

        self._playwright = sync_playwright().start()

        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=target_dir,
                headless=self.headless,
                viewport=self.viewport,
                user_agent=self.user_agent,
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                args=launch_args,
                ignore_default_args=["--enable-automation"],
                accept_downloads=True,
            )
        except Exception as e:
            # If persistent directory failed (e.g. process singleton lock conflict), fall back to ephemeral
            if not self.ephemeral:
                logger.warning(
                    "Persistent context launch failed on %s (%s). Falling back to isolated ephemeral profile...",
                    target_dir,
                    e,
                )
                self._temp_dir = tempfile.mkdtemp(prefix="playwright_fallback_")
                self._context = self._playwright.chromium.launch_persistent_context(
                    user_data_dir=self._temp_dir,
                    headless=self.headless,
                    viewport=self.viewport,
                    user_agent=self.user_agent,
                    locale="pt-BR",
                    timezone_id="America/Sao_Paulo",
                    args=launch_args,
                    ignore_default_args=["--enable-automation"],
                    accept_downloads=True,
                )
            else:
                raise

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

        if self._temp_dir and Path(self._temp_dir).exists():
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            except Exception as e:
                logger.warning("Error cleaning temporary browser dir %s: %s", self._temp_dir, e)
            self._temp_dir = None

        if not self.ephemeral and self.user_data_dir:
            clean_chromium_locks(self.user_data_dir)

        logger.info("Browser session closed.")
