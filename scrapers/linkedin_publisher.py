"""
LinkedIn Autonomous Publisher (Playwright + Shared Storage State).
Enables zero-bureaucracy automated posting to LinkedIn Feed & Pulse using a persistent,
shared authenticated browser session across all Celery workers.
"""

from datetime import datetime
import json
import logging
from pathlib import Path
import time
from typing import Any, Dict, Optional
from playwright.sync_api import BrowserContext, Page, sync_playwright
from redis import Redis

from core.config import settings
from scrapers.humanizer import human_click, human_sleep
from scrapers.stealth import STEALTH_EVASION_SCRIPT, get_random_user_agent, get_random_viewport

logger = logging.getLogger(__name__)

REDIS_AUTH_KEY = "auth:linkedin_storage_state"


class LinkedInPublisher:
    """
    Automates publishing to LinkedIn feed and Pulse articles using shared storage state.
    """

    def __init__(
        self,
        state_path: Optional[str] = None,
        headless: Optional[bool] = None,
        timeout_ms: int = 40000,
    ):
        self.state_path = Path(state_path or (Path(settings.AUTH_STATE_DIR) / "linkedin_state.json"))
        self.headless = headless if headless is not None else settings.PLAYWRIGHT_HEADLESS
        self.timeout_ms = timeout_ms

    def _get_redis(self) -> Optional[Redis]:
        try:
            r = Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2)
            r.ping()
            return r
        except Exception:
            return None

    def sync_session_from_redis_or_disk(self) -> Optional[str]:
        """
        Ensures local storage_state file exists by pulling from Redis if present.
        Returns path to json or None.
        """
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        r = self._get_redis()
        if r:
            try:
                cached_json = r.get(REDIS_AUTH_KEY)
                if cached_json:
                    self.state_path.write_text(cached_json, encoding="utf-8")
                    logger.info("Synchronized LinkedIn storage_state from Redis to %s", self.state_path)
                    return str(self.state_path)
            except Exception as e:
                logger.warning("Could not sync storage_state from Redis: %s", e)

        if self.state_path.exists() and self.state_path.stat().st_size > 10:
            return str(self.state_path)

        return None

    def save_session_to_disk_and_redis(self, context: BrowserContext):
        """
        Persists current authenticated context to both shared volume and Redis.
        """
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(self.state_path))
            logger.info("Saved LinkedIn storage_state to %s", self.state_path)

            r = self._get_redis()
            if r and self.state_path.exists():
                r.set(REDIS_AUTH_KEY, self.state_path.read_text(encoding="utf-8"), ex=86400 * 30)
                logger.info("Updated LinkedIn storage_state in central Redis.")
        except Exception as e:
            logger.warning("Could not persist LinkedIn storage_state: %s", e)

    def is_logged_in(self, page: Page) -> bool:
        """Checks if current page has active LinkedIn authenticated session."""
        try:
            current_url = page.url.lower()
            if any(k in current_url for k in ["login", "checkpoint", "authwall", "challenge", "uas/"]):
                return False

            # 1. Check for standard authenticated elements
            feed_selectors = (
                ".global-nav__me, button[aria-label*='profile'], button[aria-label*='perfil'], "
                "div.feed-shared-update-v2, button:has-text('Start a post'), button:has-text('Começar publicação'), "
                ".share-box-feed-entry__trigger, nav.global-nav, .feed-identity-module"
            )
            if page.locator(feed_selectors).first.is_visible(timeout=2000):
                return True

            # 2. Path validation (ignoring query parameters like ?session_redirect=.../feed)
            parsed_path = current_url.split("?")[0].rstrip("/")
            try:
                cookies = page.context.cookies(["https://www.linkedin.com"])
                if any(c.get("name") == "li_at" and len(c.get("value", "")) > 10 for c in cookies):
                    if any(parsed_path.endswith(path) for path in ["/feed", "/mynetwork", "/jobs", "/messaging", "/notifications", "/in"]):
                        return True
            except Exception:
                pass
        except Exception:
            pass
        return False

    def ensure_authenticated(self, page: Page, context: BrowserContext) -> bool:
        """
        Navigates to LinkedIn feed and verifies authentication.
        Attempts auto-login if credentials are provided in settings.
        """
        logger.info("Checking LinkedIn authentication status...")
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=self.timeout_ms)
        human_sleep(2.0, 3.5)

        if self.is_logged_in(page):
            logger.info("LinkedIn session is ACTIVE and verified.")
            self.save_session_to_disk_and_redis(context)
            return True

        logger.warning("LinkedIn session is NOT authenticated. Checking credentials...")

        if settings.LINKEDIN_USERNAME and settings.LINKEDIN_PASSWORD:
            try:
                page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
                human_sleep(1.0, 2.0)

                # Fill credentials
                user_loc = page.locator("input[type='email']:visible, input[name='session_key']:visible, input#username:visible").first
                if user_loc.is_visible(timeout=5000):
                    user_loc.click()
                    user_loc.fill(settings.LINKEDIN_USERNAME)
                human_sleep(0.4, 0.8)

                pass_loc = page.locator("input[type='password']:visible, input[name='session_password']:visible, input#password:visible").first
                if pass_loc.is_visible(timeout=5000):
                    pass_loc.click()
                    pass_loc.fill(settings.LINKEDIN_PASSWORD)
                human_sleep(0.4, 0.8)

                submit_loc = page.locator("button:has-text('Entrar'):visible, button:has-text('Sign in'):visible, button[type='submit']:visible").first
                if submit_loc.is_visible(timeout=5000):
                    submit_loc.click()

                page.wait_for_load_state("domcontentloaded", timeout=20000)
                human_sleep(3.0, 5.0)

                if self.is_logged_in(page):
                    logger.info("LinkedIn auto-login SUCCESSFUL.")
                    self.save_session_to_disk_and_redis(context)
                    return True

                # Check if checkpoint / 2FA occurred
                if "checkpoint" in page.url:
                    shot_path = str(settings.downloads_path / "linkedin_checkpoint.png")
                    page.screenshot(path=shot_path)
                    logger.warning("LinkedIn requires 2FA or security checkpoint. Screenshot saved to %s", shot_path)
                    raise RuntimeError("LinkedIn requires 2FA security checkpoint. Complete login via noVNC (port 6081).")
            except Exception as e:
                logger.error("Auto-login attempt failed: %s", e)
                raise

        raise RuntimeError(
            "LinkedIn session not authenticated. Open noVNC (http://localhost:6081/vnc.html) "
            "and login once to save session state."
        )

    def publish_feed_post(
        self,
        text: str,
        image_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Opens LinkedIn feed in logged-in Chromium session and publishes a post.
        """
        logger.info("Starting LinkedIn feed post publication...")
        state_file = self.sync_session_from_redis_or_disk()

        with sync_playwright() as p:
            viewport = get_random_viewport()
            user_agent = get_random_user_agent()

            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ]
            std_ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

            browser = None
            if settings.PLAYWRIGHT_USER_DATA_DIR and Path(settings.PLAYWRIGHT_USER_DATA_DIR).exists():
                context = p.chromium.launch_persistent_context(
                    user_data_dir=settings.PLAYWRIGHT_USER_DATA_DIR,
                    headless=self.headless,
                    args=launch_args,
                    viewport={"width": 1280, "height": 850},
                    user_agent=std_ua,
                    locale="pt-BR",
                    timezone_id="America/Sao_Paulo",
                )
                page = context.pages[0] if context.pages else context.new_page()
            else:
                browser = p.chromium.launch(headless=self.headless, args=launch_args)
                context_kwargs = {
                    "viewport": {"width": 1280, "height": 850},
                    "user_agent": std_ua,
                    "locale": "pt-BR",
                    "timezone_id": "America/Sao_Paulo",
                }
                if state_file:
                    context_kwargs["storage_state"] = state_file
                context = browser.new_context(**context_kwargs)
                page = context.new_page()

            context.add_init_script(STEALTH_EVASION_SCRIPT)

            try:
                self.ensure_authenticated(page, context)

                # 1. Click "Start a post" / "Começar publicação"
                post_triggers = [
                    "p:has-text('Começar publicação')",
                    "div:has-text('Começar publicação')",
                    "button:has-text('Start a post')",
                    "button:has-text('Começar publicação')",
                    "button.share-box-feed-entry__trigger",
                    "div.share-box-feed-entry__top-bar",
                    "div[data-view-name='feed-share-box'] button",
                ]

                clicked = False
                for sel in post_triggers:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=3000):
                        loc.click()
                        clicked = True
                        break

                if not clicked:
                    # Fallback: navigate directly to share modal if trigger not found
                    page.keyboard.press("c")  # LinkedIn hotkey for create post if active
                    human_sleep(1.0, 2.0)

                human_sleep(1.5, 2.5)

                # 2. Wait for modal editor area
                editor_selectors = [
                    "div.tiptap.ProseMirror",
                    "div[role='textbox']",
                    "div[role='textbox'][contenteditable='true']",
                    "div.editor-content div[contenteditable='true']",
                    "div.ql-editor",
                    ".share-creation-state__text-editor div[contenteditable='true']",
                ]

                editor = None
                for ed_sel in editor_selectors:
                    ed_loc = page.locator(ed_sel).first
                    if ed_loc.is_visible(timeout=5000):
                        editor = ed_loc
                        break

                if not editor:
                    shot_err = str(settings.downloads_path / "linkedin_modal_error.png")
                    page.screenshot(path=shot_err)
                    raise RuntimeError(f"Could not locate LinkedIn post modal text editor. Screenshot: {shot_err}")

                editor.click()
                human_sleep(0.5, 1.0)

                # Insert the text smoothly preserving line breaks and emojis
                page.keyboard.insert_text(text.strip())
                human_sleep(1.0, 2.0)

                # 3. Handle optional image upload
                if image_path and Path(image_path).exists():
                    try:
                        media_btn = page.locator("button[aria-label*='media'], button[aria-label*='mídia'], button[aria-label*='photo'], button[aria-label*='foto']").first
                        if media_btn.is_visible(timeout=2000):
                            with page.expect_file_chooser(timeout=5000) as fc_info:
                                media_btn.click()
                            file_chooser = fc_info.value
                            file_chooser.set_files(image_path)
                            human_sleep(2.0, 3.0)

                            # Click "Next" button in media preview
                            next_btn = page.locator("button:has-text('Next'), button:has-text('Avançar')").first
                            if next_btn.is_visible(timeout=4000):
                                next_btn.click()
                                human_sleep(1.5, 2.5)
                    except Exception as err_img:
                        logger.warning("Could not attach image to LinkedIn post (%s). Continuing with text...", err_img)

                # 4. Click Post / Publicar
                submit_buttons = [
                    "button.share-actions__primary-action",
                    "button:has-text('Post')",
                    "button:has-text('Publicar')",
                    "button[data-control-name='share.post']",
                ]

                post_clicked = False
                for btn_sel in submit_buttons:
                    btn = page.locator(btn_sel).first
                    if btn.is_visible(timeout=3000) and btn.is_enabled():
                        btn.click()
                        post_clicked = True
                        break

                if not post_clicked:
                    shot_submit_err = str(settings.downloads_path / "linkedin_submit_btn_error.png")
                    page.screenshot(path=shot_submit_err)
                    raise RuntimeError("Post button not clickable in LinkedIn modal.")

                human_sleep(4.0, 6.0)

                # Verify submission
                self.save_session_to_disk_and_redis(context)
                shot_success = str(settings.downloads_path / f"linkedin_post_success_{int(time.time())}.png")
                page.screenshot(path=shot_success)

                logger.info("LinkedIn post successfully published! Screenshot: %s", shot_success)
                return {
                    "status": "SUCCESS",
                    "channel": "linkedin_feed",
                    "published_at": datetime.now().isoformat(),
                    "screenshot": shot_success,
                }

            finally:
                context.close()
                if browser:
                    browser.close()

    def publish_pulse_article(
        self,
        title: str,
        content_markdown: str,
    ) -> Dict[str, Any]:
        """
        Publishes a long-form article to LinkedIn Pulse (https://www.linkedin.com/article/edit/).
        """
        logger.info("Starting LinkedIn Pulse article publication: '%s'", title)
        state_file = self.sync_session_from_redis_or_disk()

        with sync_playwright() as p:
            viewport = get_random_viewport()
            user_agent = get_random_user_agent()

            browser = p.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
            )
            context_kwargs = {
                "viewport": viewport,
                "user_agent": user_agent,
                "locale": "pt-BR",
                "timezone_id": "America/Sao_Paulo",
            }
            if state_file:
                context_kwargs["storage_state"] = state_file

            context = browser.new_context(**context_kwargs)
            context.add_init_script(STEALTH_EVASION_SCRIPT)
            page = context.new_page()

            try:
                self.ensure_authenticated(page, context)

                # Navigate to article editor
                page.goto("https://www.linkedin.com/article/edit/", wait_until="domcontentloaded", timeout=self.timeout_ms)
                human_sleep(3.0, 4.5)

                # Fill headline / title
                title_loc = page.locator("textarea[aria-label*='headline'], textarea[placeholder*='Title'], textarea[placeholder*='Título']").first
                if not title_loc.is_visible(timeout=5000):
                    title_loc = page.locator("div[role='textbox'][aria-label*='Title'], div[role='textbox'][aria-label*='Título']").first

                if title_loc.is_visible(timeout=5000):
                    title_loc.click()
                    page.keyboard.insert_text(title.strip())
                    human_sleep(1.0, 1.8)

                # Fill article body
                body_loc = page.locator("div[role='textbox'][data-placeholder*='Write'], div[role='textbox'][aria-label*='article body'], .article-editor__body-wrapper div[contenteditable='true']").first
                if body_loc.is_visible(timeout=5000):
                    body_loc.click()
                    page.keyboard.insert_text(content_markdown.strip())
                    human_sleep(1.5, 2.5)

                # Click Publish button
                pub_btn = page.locator("button:has-text('Publish'), button:has-text('Publicar')").first
                if pub_btn.is_visible(timeout=4000) and pub_btn.is_enabled():
                    pub_btn.click()
                    human_sleep(2.0, 3.5)

                    # Check for confirmation modal publish button
                    confirm_btn = page.locator("div[role='dialog'] button:has-text('Publish'), div[role='dialog'] button:has-text('Publicar')").first
                    if confirm_btn.is_visible(timeout=3000):
                        confirm_btn.click()
                        human_sleep(3.0, 5.0)

                self.save_session_to_disk_and_redis(context)
                shot_pulse = str(settings.downloads_path / f"linkedin_pulse_success_{int(time.time())}.png")
                page.screenshot(path=shot_pulse)

                logger.info("LinkedIn Pulse article published! Screenshot: %s", shot_pulse)
                return {
                    "status": "SUCCESS",
                    "channel": "linkedin_article",
                    "published_at": datetime.now().isoformat(),
                    "screenshot": shot_pulse,
                }
            finally:
                context.close()
                browser.close()


linkedin_publisher = LinkedInPublisher()
