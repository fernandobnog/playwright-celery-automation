"""
LinkedIn Autonomous Publisher (Playwright + Shared Storage State).
Enables zero-bureaucracy automated posting to LinkedIn Feed & Pulse using a persistent,
shared authenticated browser session across all Celery workers.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.request
import markdown
from playwright.sync_api import BrowserContext, Page, sync_playwright
from redis import Redis

from core.config import settings
from scrapers.humanizer import (
    human_click,
    human_idle_wander,
    human_scroll,
    human_sleep,
    human_type,
)
from scrapers.stealth import STEALTH_EVASION_SCRIPT, get_random_user_agent, get_random_viewport

logger = logging.getLogger(__name__)

REDIS_AUTH_KEY = "auth:linkedin_storage_state"
PERSISTENT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def markdown_to_linkedin_pulse_html(md_text: str, blog_url: Optional[str] = None) -> str:
    """
    Converts raw Markdown from the editorial pipeline into clean, semantic HTML
    specifically structured for LinkedIn Pulse's ProseMirror editor.
    Strips raw divider lines, repeated H1/H2 titles, metadata lines, and formats
    subtitles, headings (H3), bold/italic, lists, and interactive anchor tags (<a href>).
    """
    lines = []
    skip_header = True
    for line in md_text.splitlines():
        trimmed = line.strip()
        # Strip divider lines (e.g. -------------------- or ====================)
        if re.match(r'^[=\-_\*]{3,}$', trimmed):
            continue
        if trimmed.startswith("CANAL ") or "ARTIGO COMPLETO" in trimmed:
            continue
        if (
            trimmed.startswith("⏱️")
            or trimmed.startswith("🖼️")
            or trimmed.lower().startswith("sugestão de imagem")
            or trimmed.lower().startswith("tempo de leitura")
        ):
            continue
        # Strip leading # Title, ## Title, and *Subtitle* if repeated at the top of the body
        if skip_header and (trimmed.startswith("# ") or trimmed.startswith("## ") or trimmed.startswith("*") or not trimmed):
            continue
        if trimmed:
            skip_header = False
        lines.append(line)

    cleaned_md = "\n".join(lines).strip()

    # In LinkedIn Pulse, H3 is the primary section heading
    cleaned_md = re.sub(r"^#\s+", "### ", cleaned_md, flags=re.MULTILINE)
    cleaned_md = re.sub(r"^##\s+", "### ", cleaned_md, flags=re.MULTILINE)

    html_output = markdown.markdown(cleaned_md, extensions=["extra", "sane_lists"])

    # Ensure blog CTA link is present at the end
    if blog_url and blog_url not in html_output:
        html_output += (
            f"<p>Confira também o ensaio completo e referências técnicas detalhadas em meu blog:<br>"
            f'<a href="{blog_url}">{blog_url}</a></p>'
        )

    return html_output


def parse_post_age(post_id_or_url: str, time_text: str = "", max_age_days: float = 5.0) -> tuple[bool, float, Optional[str]]:
    """
    Validates whether a LinkedIn post was published within max_age_days (default: 5.0 days).
    Uses both LinkedIn Snowflake 19-digit timestamp (id >> 22) and textual relative timestamp.
    Returns (is_recent, age_days, post_iso_datetime).
    """
    now_utc = datetime.now(timezone.utc)
    snowflake_age = None
    post_dt = None

    # 1. Extract 19-digit snowflake integer
    match = re.search(r'(?:urn:li:(?:activity|ugcPost|share):|/feed/update/urn:li:(?:activity|ugcPost|share):)?([7]\d{17,19})', post_id_or_url or '')
    if match:
        try:
            num_id = int(match.group(1))
            ms = num_id >> 22
            if 1577836800000 <= ms <= 1893456000000:
                post_dt = datetime.fromtimestamp(ms / 1000.0, timezone.utc)
                diff = (now_utc - post_dt).total_seconds() / 86400.0
                snowflake_age = max(0.0, diff)
        except Exception:
            pass

    # 2. Textual relative time
    text_age = None
    if time_text:
        tt = time_text.lower().strip()
        if any(w in tt for w in ['agora', 'just now', 'min', 'm ']):
            text_age = 0.05
        t_match = re.search(r'(\d+)\s*(h|d|sem|m|min|mo|yr|a|s|w)\b', tt)
        if t_match:
            val = int(t_match.group(1))
            unit = t_match.group(2)
            if unit in ['s', 'min']:
                text_age = 0.01
            elif unit == 'h':
                text_age = val / 24.0
            elif unit == 'd':
                text_age = float(val)
            elif unit in ['sem', 'w']:
                text_age = float(val * 7)
            elif unit in ['m', 'mo']:
                text_age = float(val * 30)
            elif unit in ['a', 'yr', 'y']:
                text_age = float(val * 365)

    if text_age is not None:
        age = text_age
    elif snowflake_age is not None:
        age = snowflake_age
    else:
        return False, 999.0, None

    is_recent = age <= max_age_days
    return is_recent, age, post_dt.isoformat() if post_dt else None


def resolve_linkedin_url(url: str) -> str:
    """Follows lnkd.in shortlink redirects to retrieve the canonical LinkedIn post URL."""
    if not url:
        return url
    if "lnkd.in" in url:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": PERSISTENT_USER_AGENT})
            with urllib.request.urlopen(req, timeout=5) as resp:
                resolved = resp.url
                if resolved and "linkedin.com" in resolved:
                    return resolved.split("?")[0]
        except Exception:
            pass
    return url.split("?")[0]


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

    def _inject_cookies_into_context(self, context: BrowserContext) -> None:
        """Loads cookies from storage_state file or central Redis into browser context."""
        try:
            if self.state_path.exists() and self.state_path.stat().st_size > 10:
                with open(self.state_path, "r", encoding="utf-8") as sf:
                    state_data = json.load(sf)
                    cookies = state_data.get("cookies", [])
                    if cookies:
                        context.add_cookies(cookies)
                        logger.info("Injected %d cookies from storage_state into context", len(cookies))
        except Exception as err_ck:
            logger.warning("Could not inject cookies into context: %s", err_ck)

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
            if any(k in current_url for k in ["login", "checkpoint", "authwall", "challenge"]):
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
        self._inject_cookies_into_context(context)
        logger.info("Checking LinkedIn authentication status...")
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=self.timeout_ms)
        human_sleep(2.0, 3.5)

        # Check for intermediate 'We are signing you in' / 'Estamos dando acesso' or single-sign-on redirect
        for attempt in range(15):
            if self.is_logged_in(page):
                break
            try:
                current_url = page.url.lower()
                page_text = ""
                try:
                    page_text = page.content().lower()
                except Exception:
                    pass  # Frame navigation may be in progress

                is_transition = (
                    "session_redirect" in current_url
                    or any(phrase in current_url for phrase in ["uas/login", "checkpoint/lg"])
                    or any(phrase in page_text for phrase in ["signing you in", "dando acesso", "estamos dando", "loading"])
                )
                if is_transition:
                    logger.info("Detected LinkedIn transition/redirect in progress (%d/15, url=%s). Waiting for auto-redirect...", attempt + 1, current_url)
                human_sleep(1.8, 2.5)
            except Exception:
                human_sleep(1.8, 2.5)

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

    def _create_resilient_context(
        self,
        p,
        state_file: Optional[str] = None,
        viewport: Optional[Dict[str, int]] = None,
        permissions: Optional[List[str]] = None,
    ) -> Tuple[Optional[Any], Any, Any]:
        """
        Creates a Chromium browser and context with resilient concurrency fallback.
        Attempts launch_persistent_context first if configured. If the persistent
        profile is locked or in use by another concurrent task/worker, gracefully
        falls back to an isolated browser instance using the synchronized storage_state.
        """
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
        ]
        vp = viewport or {"width": 1280, "height": 850}
        browser = None

        if settings.PLAYWRIGHT_USER_DATA_DIR and Path(settings.PLAYWRIGHT_USER_DATA_DIR).exists():
            try:
                persistent_kwargs: Dict[str, Any] = {
                    "user_data_dir": settings.PLAYWRIGHT_USER_DATA_DIR,
                    "headless": self.headless,
                    "args": launch_args,
                    "viewport": vp,
                    "user_agent": PERSISTENT_USER_AGENT,
                    "locale": "pt-BR",
                    "timezone_id": "America/Sao_Paulo",
                }
                if permissions:
                    persistent_kwargs["permissions"] = permissions
                context = p.chromium.launch_persistent_context(**persistent_kwargs)
                page = context.pages[0] if context.pages else context.new_page()
                context.add_init_script(STEALTH_EVASION_SCRIPT)
                return browser, context, page
            except Exception as exc:
                logger.warning(
                    "Persistent browser profile in use or locked (%s). Falling back to isolated browser context.",
                    exc,
                )

        # Isolated fallback using synchronized storage_state
        browser = p.chromium.launch(headless=self.headless, args=launch_args)
        context_kwargs: Dict[str, Any] = {
            "viewport": vp,
            "user_agent": PERSISTENT_USER_AGENT,
            "locale": "pt-BR",
            "timezone_id": "America/Sao_Paulo",
        }
        if state_file:
            context_kwargs["storage_state"] = state_file
        if permissions:
            context_kwargs["permissions"] = permissions
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        context.add_init_script(STEALTH_EVASION_SCRIPT)
        return browser, context, page

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
            browser, context, page = self._create_resilient_context(p, state_file=state_file, viewport=viewport)

            try:
                self.ensure_authenticated(page, context)

                # Organic warm-up scroll and idle gaze on feed before opening post modal
                try:
                    human_scroll(page, steps=random.randint(2, 3), min_distance=100, max_distance=260)
                    page.mouse.wheel(0, -120)
                    human_idle_wander(page, duration_sec=random.uniform(1.5, 3.0))
                except Exception:
                    pass

                # 1. Click "Start a post" / "Começar publicação" with Bézier mouse motion
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
                        human_click(page, loc)
                        clicked = True
                        break

                if not clicked:
                    # Fallback: navigate directly to share modal if trigger not found
                    page.keyboard.press("c")  # LinkedIn hotkey for create post if active
                    human_sleep(1.5, 2.5)

                human_sleep(2.0, 3.5)

                # 2. Wait for modal editor area (allowing for spinner to resolve)
                editor_selectors = [
                    "div.tiptap.ProseMirror",
                    "div[role='textbox']",
                    "div[role='textbox'][contenteditable='true']",
                    "div.editor-content div[contenteditable='true']",
                    "div.ql-editor",
                    ".share-creation-state__text-editor div[contenteditable='true']",
                ]

                editor = None
                for _ in range(25):
                    for ed_sel in editor_selectors:
                        ed_loc = page.locator(ed_sel).first
                        try:
                            if ed_loc.is_visible():
                                editor = ed_loc
                                break
                        except Exception:
                            pass
                    if editor:
                        break
                    time.sleep(1.0)

                if not editor:
                    shot_err = str(settings.downloads_path / "linkedin_modal_error.png")
                    page.screenshot(path=shot_err)
                    raise RuntimeError(f"Could not locate LinkedIn post modal text editor. Screenshot: {shot_err}")

                # Clean text: strip raw divider lines, trailing CANAL headers, and markdown asterisks
                clean_feed_lines = []
                for line in text.splitlines():
                    trimmed = line.strip()
                    if re.match(r'^[=\-_\*]{3,}$', trimmed):
                        continue
                    if trimmed.startswith("CANAL "):
                        continue
                    clean_feed_lines.append(line)
                clean_feed_text = "\n".join(clean_feed_lines).strip()
                clean_feed_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean_feed_text)
                clean_feed_text = re.sub(r'(?<!\w)\*([^*]+)\*(?!\w)', r'\1', clean_feed_text)

                # Focus editor and type character by character with non-uniform frequency
                human_idle_wander(page, duration_sec=random.uniform(1.0, 2.2))
                logger.info("Typing feed post with non-uniform human cadence (%d chars)...", len(clean_feed_text))
                human_type(page, editor, clean_feed_text, min_delay_ms=75, max_delay_ms=220)
                human_sleep(2.0, 4.0)

                # 3. Handle optional image upload
                if image_path and Path(image_path).exists():
                    try:
                        media_btn = page.locator("button[aria-label*='media'], button[aria-label*='mídia'], button[aria-label*='photo'], button[aria-label*='foto']").first
                        if media_btn.is_visible(timeout=2000):
                            with page.expect_file_chooser(timeout=5000) as fc_info:
                                human_click(page, media_btn)
                            file_chooser = fc_info.value
                            file_chooser.set_files(image_path)
                            human_sleep(2.5, 4.0)

                            # Click "Next" button in media preview
                            next_btn = page.locator("button:has-text('Next'), button:has-text('Avançar')").first
                            for _ in range(10):
                                if next_btn.is_visible():
                                    human_click(page, next_btn)
                                    human_sleep(1.8, 3.0)
                                    break
                                time.sleep(1.0)
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
                for _ in range(15):
                    for btn_sel in submit_buttons:
                        btn = page.locator(btn_sel).first
                        try:
                            if btn.is_visible() and btn.is_enabled():
                                human_idle_wander(page, duration_sec=random.uniform(2.0, 4.0))
                                human_click(page, btn)
                                post_clicked = True
                                break
                        except Exception:
                            pass
                    if post_clicked:
                        break
                    time.sleep(1.0)

                if not post_clicked:
                    shot_submit_err = str(settings.downloads_path / "linkedin_submit_btn_error.png")
                    page.screenshot(path=shot_submit_err)
                    raise RuntimeError("Post button not clickable in LinkedIn modal.")

                human_sleep(5.0, 8.0)

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
        image_path: Optional[str] = None,
        blog_url: Optional[str] = None,
        share_hook: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Publishes a long-form article to LinkedIn Pulse (https://www.linkedin.com/article/new/).
        Supports optional cover image upload, headline typing, rich HTML body insertion into ProseMirror,
        and final network distribution via the share modal.
        """
        logger.info("Starting LinkedIn Pulse article publication: '%s'", title)
        state_file = self.sync_session_from_redis_or_disk()

        with sync_playwright() as p:
            viewport = get_random_viewport()

            browser, context, page = self._create_resilient_context(p, state_file=state_file, viewport=viewport)

            try:
                self.ensure_authenticated(page, context)

                # 1. Navigate to modern article editor
                page.goto("https://www.linkedin.com/article/new/", wait_until="domcontentloaded", timeout=self.timeout_ms)
                human_sleep(3.5, 5.0)
                human_idle_wander(page, duration_sec=random.uniform(1.5, 3.0))

                # 2. Optional: Upload cover image
                if image_path and Path(image_path).exists():
                    try:
                        cover_btn = page.locator("button:has-text('Carregar do computador'), button:has-text('Upload from computer')").first
                        if cover_btn.is_visible(timeout=4000):
                            human_click(page, cover_btn)
                            human_sleep(1.5, 2.5)

                            # In modern LinkedIn Pulse editor, clicking the cover button opens a modal dialog
                            file_input = page.locator("div[role='dialog'] input[type='file'], input[type='file']").first
                            if file_input.count() > 0:
                                file_input.set_input_files(image_path)
                                human_sleep(2.0, 3.5)
                            else:
                                modal_upload_btn = page.locator("div[role='dialog'] button:has-text('Carregar a partir do computador'), div[role='dialog'] button:has-text('Upload from computer')").first
                                if modal_upload_btn.is_visible(timeout=3000):
                                    with page.expect_file_chooser(timeout=5000) as fc_info:
                                        human_click(page, modal_upload_btn)
                                    fc_info.value.set_files(image_path)
                                    human_sleep(2.0, 3.5)

                            # Click modal Avançar / Salvar
                            modal_apply = page.locator("div[role='dialog'] button:has-text('Avançar'), div.artdeco-modal button:has-text('Avançar')").first
                            for _ in range(10):
                                if modal_apply.is_visible() and modal_apply.is_enabled():
                                    human_click(page, modal_apply)
                                    human_sleep(2.5, 4.0)
                                    logger.info("Cover image attached to Pulse article: %s", image_path)
                                    break
                                time.sleep(1.0)
                    except Exception as err_cover:
                        logger.warning("Could not attach cover image to Pulse article (%s). Continuing with text...", err_cover)
                    finally:
                        # Ensure any modal left open is closed so it doesn't block the rest of the editor
                        close_btn = page.locator("div[role='dialog'] button[aria-label*='Fechar'], div[role='dialog'] button[aria-label*='Close'], div.artdeco-modal button[aria-label*='Fechar']").first
                        if close_btn.is_visible():
                            try:
                                close_btn.click()
                                human_sleep(1.0, 2.0)
                            except Exception:
                                pass

                # 3. Fill headline / title
                title_selectors = [
                    "textarea.article-editor-headline__textarea",
                    "textarea[placeholder*='Título']",
                    "textarea[placeholder*='Title']",
                    "textarea[aria-label*='headline']",
                ]
                title_loc = None
                for _ in range(15):
                    for sel in title_selectors:
                        loc = page.locator(sel).first
                        try:
                            if loc.is_visible():
                                title_loc = loc
                                break
                        except Exception:
                            pass
                    if title_loc:
                        break
                    time.sleep(1.0)

                if not title_loc:
                    shot_err = str(settings.downloads_path / "linkedin_pulse_title_err.png")
                    page.screenshot(path=shot_err)
                    raise RuntimeError(f"Could not find title field in article editor. Screenshot: {shot_err}")

                logger.info("Typing Pulse article title with non-uniform human cadence: '%s'", title)
                human_type(page, title_loc, title.strip(), min_delay_ms=35, max_delay_ms=90)
                human_sleep(1.0, 2.0)

                # Fallback verification: ensure title field is genuinely populated
                if not title_loc.input_value().strip():
                    logger.warning("Title empty after human_type, forcing fill.")
                    title_loc.fill(title.strip())
                    human_sleep(0.5, 1.0)

                # 4. Clean and fill article body text into ProseMirror using rich HTML
                article_html = markdown_to_linkedin_pulse_html(content_markdown, blog_url)

                body_loc = None
                body_selectors = [
                    "div.ProseMirror p.article-editor-paragraph",
                    "div.ProseMirror[contenteditable='true']",
                    "div[data-placeholder*='artigo']",
                    "div[data-placeholder*='article']",
                ]
                for _ in range(15):
                    for b_sel in body_selectors:
                        b_cand = page.locator(b_sel).first
                        try:
                            if b_cand.is_visible():
                                body_loc = b_cand
                                break
                        except Exception:
                            pass
                    if body_loc:
                        break
                    time.sleep(1.0)

                if not body_loc:
                    shot_err = str(settings.downloads_path / "linkedin_pulse_body_err.png")
                    page.screenshot(path=shot_err)
                    raise RuntimeError(f"Could not find body area in article editor. Screenshot: {shot_err}")

                human_click(page, body_loc)
                human_sleep(0.8, 1.6)

                # Clear default paragraph
                page.keyboard.press("Control+A")
                human_sleep(0.3, 0.6)
                page.keyboard.press("Backspace")
                human_sleep(0.5, 1.0)

                # Paste rich HTML cleanly into ProseMirror
                page.evaluate('''html => {
                    const editor = document.querySelector('div.ProseMirror');
                    const dt = new DataTransfer();
                    dt.setData('text/html', html);
                    dt.setData('text/plain', (new DOMParser()).parseFromString(html, 'text/html').body.innerText);
                    const pasteEvent = new ClipboardEvent('paste', {
                        bubbles: true,
                        cancelable: true,
                        clipboardData: dt
                    });
                    editor.dispatchEvent(pasteEvent);
                }''', article_html)
                
                # Organic reading and review scroll across long draft
                human_sleep(2.5, 4.0)
                human_scroll(page, steps=3, min_distance=150, max_distance=300)
                human_sleep(1.5, 3.0)
                human_scroll(page, steps=2, min_distance=-300, max_distance=-150)
                human_sleep(2.0, 3.5)

                # 5. Click Avançar button (top right)
                next_btn_selectors = [
                    "button.article-editor-nav__publish",
                    "button:has-text('Avançar')",
                    "button:has-text('Next')",
                ]
                next_btn = None
                for _ in range(15):
                    for n_sel in next_btn_selectors:
                        n_cand = page.locator(n_sel).first
                        try:
                            if n_cand.is_visible() and n_cand.is_enabled():
                                next_btn = n_cand
                                break
                        except Exception:
                            pass
                    if next_btn:
                        break
                    time.sleep(1.0)

                if not next_btn:
                    shot_err = str(settings.downloads_path / "linkedin_pulse_next_err.png")
                    page.screenshot(path=shot_err)
                    raise RuntimeError("Avançar button not ready in article editor.")

                human_idle_wander(page, duration_sec=random.uniform(1.8, 2.8))
                logger.info("Clicking Avançar button to open share modal...")
                next_btn.click(timeout=8000)
                human_sleep(3.5, 5.5)

                # Wait for share modal to appear
                dialog_loc = page.locator("div[role='dialog'], div.artdeco-modal").first
                for _ in range(12):
                    try:
                        if dialog_loc.is_visible():
                            break
                    except Exception:
                        pass
                    time.sleep(1.0)

                # 6. In the post-sharing modal, add optional hook and click primary Publicar
                share_input = page.locator("div[role='dialog'] div[role='textbox'], div[role='dialog'] div.ProseMirror, div[role='dialog'] div.tiptap, div[role='dialog'] [contenteditable='true'], div[role='dialog'] div.ql-editor").first
                if share_input.is_visible(timeout=5000):
                    if share_hook and share_hook.strip():
                        clean_hook = share_hook.strip()
                        clean_hook = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean_hook)
                        clean_hook = re.sub(r'(?<!\w)\*([^*]+)\*(?!\w)', r'\1', clean_hook)
                        clean_hook = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', clean_hook)
                        clean_hook = re.sub(r'^[=\-_\*]{3,}$', '', clean_hook, flags=re.MULTILINE)
                        clean_hook = re.sub(r'\n{3,}', '\n\n', clean_hook)
                        final_hook = clean_hook.strip()
                    else:
                        final_hook = (
                            f"Novo artigo no ar: {title.strip()}.\n\n"
                            f"Compartilho uma reflexão prática e técnica sobre os impactos reais dessa transformação "
                            f"na engenharia, liderança e operações corporativas.\n\n"
                            f"Confira a análise completa no cartão abaixo e deixe sua perspectiva nos comentários."
                        )
                    logger.info("Typing share modal hook with human cadence (%d chars)...", len(final_hook))
                    human_type(page, share_input, final_hook, min_delay_ms=35, max_delay_ms=90)
                    human_sleep(2.0, 3.5)

                # Click primary action publish button (not the audience settings button)
                pub_btn_selectors = [
                    "button.share-actions__primary-action",
                    "div[role='dialog'] button.artdeco-button--primary:has-text('Publicar')",
                    "div[role='dialog'] button:has-text('Publicar')",
                    "button:has-text('Publicar')",
                    "button:has-text('Post')",
                ]
                pub_btn = None
                for _ in range(15):
                    for p_sel in pub_btn_selectors:
                        p_cand = page.locator(p_sel).first
                        try:
                            if p_cand.is_visible() and p_cand.is_enabled():
                                pub_btn = p_cand
                                break
                        except Exception:
                            pass
                    if pub_btn:
                        break
                    time.sleep(1.0)

                if not pub_btn:
                    shot_err = str(settings.downloads_path / "linkedin_pulse_pub_err.png")
                    page.screenshot(path=shot_err)
                    raise RuntimeError("Final Publicar button not found or enabled in share dialog.")

                human_idle_wander(page, duration_sec=random.uniform(1.8, 3.0))
                logger.info("Clicking final Publicar button in share dialog...")
                pub_btn.click(timeout=8000)
                human_sleep(8.0, 12.0)

                self.save_session_to_disk_and_redis(context)
                shot_pulse = str(settings.downloads_path / f"linkedin_pulse_success_{int(time.time())}.png")
                page.screenshot(path=shot_pulse)

                published_url = page.url
                logger.info("LinkedIn Pulse article published! URL: %s | Screenshot: %s", published_url, shot_pulse)
                return {
                    "status": "SUCCESS",
                    "channel": "linkedin_article",
                    "url": published_url,
                    "published_at": datetime.now().isoformat(),
                    "screenshot": shot_pulse,
                }
            finally:
                context.close()
                if browser:
                    browser.close()

    def get_latest_post_from_profile(
        self,
        profile_url: str,
        max_age_days: float = 5.0,
        target_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Navigates to the target profile's recent activity feed and extracts the latest publication
        strictly authored/posted within max_age_days (default: 5.0 days).
        Filters out reactions/comments on third-party posts and old publications.
        Returns a dict with post_id, post_url, text, author, time_text, age_days, or None if no recent post found.
        """
        logger.info("Checking latest activity for profile: %s (max_age_days=%.1f, target=%s)", profile_url, max_age_days, target_name)
        state_file = self.sync_session_from_redis_or_disk()

        with sync_playwright() as p:
            browser, context, page = self._create_resilient_context(
                p,
                state_file=state_file,
                permissions=["clipboard-read", "clipboard-write"],
            )

            try:
                self.ensure_authenticated(page, context)

                clean_profile = profile_url.rstrip("/")
                activity_url = clean_profile + "/recent-activity/all/"
                logger.info("Visiting activity feed: %s", activity_url)
                page.goto(activity_url, wait_until="domcontentloaded", timeout=30000)
                human_sleep(2.0, 3.5)

                # Check if profile or activity feed does not exist (404)
                body_text = page.locator("body").inner_text()
                if any(msg in body_text for msg in ["Esta página não existe", "Page not found", "Não foi possível encontrar"]):
                    logger.warning("Target profile activity feed not found (404): %s", activity_url)
                    return None

                try:
                    human_scroll(page, steps=random.randint(1, 2), min_distance=150, max_distance=300)
                except Exception:
                    pass

                # Expand 'mais' / 'see more' buttons to unhide full post text
                page.evaluate("""() => {
                    document.querySelectorAll('button').forEach(b => {
                        const txt = (b.innerText || '').trim().toLowerCase();
                        if (txt === 'mais' || txt === '…mais' || txt === 'ver mais' || txt === 'see more') {
                            try { b.click(); } catch(e){}
                        }
                    });
                }""")
                human_sleep(0.8, 1.2)

                # Locate feed update cards inside main feed
                card_locators = page.locator('div[role="listitem"], div.feed-shared-update-v2, li.profile-creator-shared-feed-update__container').all()
                if not card_locators:
                    card_locators = page.locator('main section li').all()

                logger.info("Found %d candidate cards in recent activity feed for %s", len(card_locators), target_name or profile_url)

                for idx, card in enumerate(card_locators[:10]):
                    card_text = card.inner_text().strip()
                    if not card_text or len(card_text) < 40:
                        continue

                    # 1. Skip reactions / comments on third-party posts (e.g. 'Daniel Becker curtiu isso')
                    lines = [l.strip() for l in card_text.split("\n") if l.strip()]
                    header_area = " ".join(lines[:4]).lower()
                    if any(act in header_area for act in [
                        "curtiu isso", "comentou isso", "apoia isso", "adorou isso", "celebrou isso",
                        "liked this", "commented on this", "celebrated this", "supports this"
                    ]):
                        logger.info("Card #%d skipped: target merely reacted to a third-party post ('%s')", idx, lines[0] if lines else "")
                        continue

                    # 2. Extract author
                    author = target_name or ""
                    author_loc = card.locator(".update-components-actor__name, .feed-shared-actor__name, h3, strong").first
                    if author_loc.count() > 0:
                        author = author_loc.inner_text().strip() or author

                    # 3. Extract time text (top-level timestamp of this post)
                    time_match = re.search(r'(\d+\s*(?:h|d|sem|m|min|mo|yr|a|s)\b(?:\s*•\s*Editado)?)', card_text, re.IGNORECASE)
                    time_text = time_match.group(1) if time_match else ""

                    # 4. Extract canonical post URL via control menu
                    canonical_url = None
                    ctrl_btn = card.locator('button[aria-label*="controle"], button[aria-label*="opções"], button[aria-label*="control"]').first
                    if ctrl_btn.is_visible():
                        try:
                            ctrl_btn.click(timeout=1000)
                            page.wait_for_timeout(400)
                            menu_items = page.locator('div[role="menu"] *').all()
                            for it in menu_items:
                                try:
                                    it_txt = it.inner_text().strip().lower()
                                    if "copiar link" in it_txt or "copy link" in it_txt:
                                        it.click(timeout=1000)
                                        page.wait_for_timeout(400)
                                        clip_text = page.evaluate("navigator.clipboard.readText()")
                                        if clip_text and ("http://" in clip_text or "https://" in clip_text):
                                            canonical_url = resolve_linkedin_url(clip_text.strip())
                                            break
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    # Fallback URL from direct card links
                    fallback_url = None
                    link_loc = card.locator('a[href*="/feed/update/"], a[href*="/posts/"]').first
                    if link_loc.count() > 0:
                        raw_href = (link_loc.get_attribute("href") or "").split("?")[0]
                        if raw_href:
                            fallback_url = raw_href

                    post_url = canonical_url or fallback_url
                    if not post_url:
                        logger.debug("Card #%d: Could not resolve a valid post URL. Skipping.", idx)
                        continue

                    # Extract post ID
                    post_id = post_url
                    if "urn:li:" in post_url:
                        post_id = "urn:li:" + post_url.split("urn:li:")[-1].split("/")[0]

                    # 5. Strict Age Verification (Maximum 5 Days)
                    is_recent, age_days, post_dt = parse_post_age(post_id, time_text, max_age_days=max_age_days)
                    logger.info("Card #%d: Time='%s' | Age=%.2fd | Recent=%s | URL=%s", idx, time_text, age_days, is_recent, post_url)

                    if not is_recent:
                        logger.info("Card #%d skipped: post is older than %.1f days (%.1fd ago).", idx, max_age_days, age_days)
                        continue

                    # 6. Extract full clean post text
                    clean_text = card.evaluate("""el => {
                        const clone = el.cloneNode(true);
                        clone.querySelectorAll('button, svg, nav, [class*="action"], [class*="social"], [class*="control"], [class*="reactions"]').forEach(n => n.remove());
                        return clone.innerText.trim();
                    }""")

                    cleaned_lines = [l.strip() for l in clean_text.split("\n") if l.strip()]
                    filtered_lines = [
                        l for l in cleaned_lines
                        if l not in ["Publicação no feed", "Seguir", "Visualizar no LinkedIn", "• 2º", "• 1º", "• 3º", "Visualizações do perfil"]
                    ]
                    post_text = "\n\n".join(filtered_lines)

                    if len(post_text) < 30:
                        logger.debug("Card #%d: Extracted text too short (%d chars). Skipping.", idx, len(post_text))
                        continue

                    logger.info(
                        "Found valid modern post (<= %.1fd) for %s: ID=%s, Time=%s, Text len=%d, URL=%s",
                        max_age_days, author, post_id, time_text, len(post_text), post_url
                    )
                    return {
                        "post_id": post_id,
                        "post_url": post_url,
                        "text": post_text,
                        "author": author,
                        "time_text": time_text,
                        "age_days": age_days,
                        "post_date": post_dt,
                    }

                logger.info("No publications within %.1f days found for %s (%s).", max_age_days, target_name or "Target", profile_url)
                return None

            except Exception as e:
                logger.error("Error inspecting profile activity (%s): %s", profile_url, e, exc_info=True)
                return None
            finally:
                context.close()
                if browser:
                    browser.close()

    def comment_on_post(self, post_url: str, comment_text: str) -> Dict[str, Any]:
        """
        Navigates to a specific LinkedIn post and publishes a sniper comment with humanized typing.
        """
        logger.info("Starting LinkedIn sniper comment publication on: %s", post_url)
        state_file = self.sync_session_from_redis_or_disk()

        with sync_playwright() as p:
            browser, context, page = self._create_resilient_context(p, state_file=state_file)

            try:
                self.ensure_authenticated(page, context)

                logger.info("Opening post URL: %s", post_url)
                page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
                human_sleep(2.5, 4.0)

                # Wait for post content or main container to settle
                try:
                    page.wait_for_selector("div.feed-shared-update-v2, article, main, div.core-rail", timeout=15000)
                except Exception:
                    pass

                try:
                    human_scroll(page, steps=random.randint(1, 2), min_distance=150, max_distance=300)
                except Exception:
                    pass

                # 1. Locate editor if already open, or trigger comment box
                editor_selectors = [
                    "div[aria-label*='coment' i][contenteditable='true']",
                    "div.comments-comment-box__editor div[contenteditable='true']",
                    "div.ql-editor[contenteditable='true']",
                    "div.comments-comment-texteditor div[contenteditable='true']",
                    "div.editor-content[contenteditable='true']",
                    "div[data-placeholder*='coment' i]",
                    "div[role='textbox'][contenteditable='true']:not(#msg-overlay *)",
                ]

                editor = None
                for sel in editor_selectors:
                    loc = page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible():
                        editor = loc
                        break

                if not editor:
                    trigger_selectors = [
                        "button[aria-label*='Comentar' i]",
                        "button:has-text('Comentar')",
                        "button:has-text('Comment')",
                        "div.comments-comment-box",
                    ]
                    for sel in trigger_selectors:
                        trig = page.locator(sel).first
                        if trig.count() > 0:
                            try:
                                trig.scroll_into_view_if_needed(timeout=5000)
                                human_sleep(0.5, 1.0)
                                human_click(page, trig)
                                human_sleep(1.5, 2.5)
                                break
                            except Exception as err_trig:
                                logger.debug("Could not click trigger %s: %s", sel, err_trig)

                    # Re-check editor after clicking trigger
                    for sel in editor_selectors:
                        loc = page.locator(sel).first
                        if loc.count() > 0 and loc.is_visible():
                            editor = loc
                            break

                if not editor:
                    shot_err = str(settings.downloads_path / f"comment_editor_not_found_{int(time.time())}.png")
                    page.screenshot(path=shot_err)
                    raise RuntimeError(f"Comment editor box not found on {post_url}. Screenshot saved: {shot_err}")

                logger.info("Typing sniper comment into editor...")
                try:
                    editor.scroll_into_view_if_needed(timeout=4000)
                except Exception as err_scroll:
                    logger.debug("Editor scroll_into_view_if_needed bypassed: %s", err_scroll)
                human_click(page, editor)
                human_sleep(0.5, 1.2)
                human_type(page, editor, comment_text, min_delay_ms=25, max_delay_ms=65)
                human_sleep(1.5, 2.5)

                # 3. Locate submit button
                submit_btn = None

                # Method A: Look inside the ancestor comment box/form/feed update
                try:
                    candidates = editor.locator("xpath=ancestor::*[contains(@class, 'comment') or contains(@class, 'feed-shared') or self::form]//button").all()
                    for b in candidates:
                        txt = b.inner_text().strip().lower()
                        if txt in ["comentar", "publicar", "post", "comment"] and b.is_enabled():
                            submit_btn = b
                            logger.info("Located submit button inside ancestor container: '%s'", txt)
                            break
                except Exception as err_cand:
                    logger.debug("Ancestor button search failed: %s", err_cand)

                # Method B: Global selectors with Portuguese and English button texts
                if not submit_btn:
                    submit_selectors = [
                        "button:has-text('Comentar'):enabled",
                        "button:has-text('Publicar'):enabled",
                        "button:has-text('Post'):enabled",
                        "button:has-text('Comment'):enabled",
                        "button.comments-comment-box__submit-button:enabled",
                        "button[aria-label*='Publicar' i]:enabled",
                    ]
                    for sel in submit_selectors:
                        candidates = page.locator(sel).all()
                        for loc in candidates:
                            txt = loc.inner_text().strip().lower()
                            if (txt in ["comentar", "publicar", "post", "comment"] or "submit" in (loc.get_attribute("class") or "")) and loc.is_enabled() and loc.is_visible():
                                submit_btn = loc
                                logger.info("Located submit button via selector '%s': '%s'", sel, txt)
                                break
                        if submit_btn:
                            break

                if not submit_btn:
                    shot_err = str(settings.downloads_path / f"comment_submit_not_found_{int(time.time())}.png")
                    page.screenshot(path=shot_err)
                    raise RuntimeError("Submit comment button not found or disabled.")

                logger.info("Clicking submit comment button...")
                try:
                    submit_btn.scroll_into_view_if_needed(timeout=4000)
                except Exception as err_scroll:
                    logger.debug("Submit button scroll_into_view_if_needed bypassed: %s", err_scroll)
                human_click(page, submit_btn)
                human_sleep(3.5, 5.5)

                shot_success = str(settings.downloads_path / f"comment_success_{int(time.time())}.png")
                page.screenshot(path=shot_success)
                self.save_session_to_disk_and_redis(context)

                logger.info("LinkedIn sniper comment successfully posted! Screenshot: %s", shot_success)
                return {
                    "status": "SUCCESS",
                    "post_url": post_url,
                    "comment_text": comment_text,
                    "published_at": datetime.now().isoformat(),
                    "screenshot": shot_success,
                }
            finally:
                context.close()
                if browser:
                    browser.close()

    def like_post(self, post_url: str) -> Dict[str, Any]:
        """
        Navigates to a specific LinkedIn post and reacts/likes it if not already reacted.
        """
        logger.info("Starting LinkedIn like reaction on: %s", post_url)
        state_file = self.sync_session_from_redis_or_disk()

        with sync_playwright() as p:
            browser, context, page = self._create_resilient_context(p, state_file=state_file)

            try:
                self.ensure_authenticated(page, context)

                logger.info("Opening post URL for like: %s", post_url)
                page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
                human_sleep(2.5, 4.0)

                # Wait for post container
                try:
                    page.wait_for_selector(
                        "div.feed-shared-update-v2, article, main, div.core-rail, div[data-fie-id]",
                        timeout=15000,
                    )
                except Exception:
                    pass

                # Locate like / reaction button with progressive scroll (posts can have tall images or long text)
                like_selectors = [
                    # Modern LinkedIn pt-BR & en reaction button
                    "button[aria-label*='botão de reação' i]",
                    "button[aria-label*='reaction button' i]",
                    "button:has(svg[id*='thumbs-up'])",
                    "button:has(svg[id*='like'])",
                    # Classic / feed post action bar selectors
                    "button.react-button__trigger",
                    "button[aria-label*='Reagir com Gostei' i]",
                    "button[aria-label*='Gostei' i]",
                    "button[aria-label*='Curtir' i]",
                    "button[aria-label*='Like' i]",
                    "div.feed-shared-social-action-bar button:has-text('Gostei')",
                    "div.feed-shared-social-action-bar button:has-text('Curtir')",
                    "div.feed-shared-social-action-bar button:has-text('Like')",
                    "span.reactions-react-button button",
                    "button:has-text('Gostei')",
                    "button:has-text('Curtir')",
                    "button:has-text('Like')",
                ]

                like_btn = None
                for _attempt in range(5):
                    for sel in like_selectors:
                        candidates = page.locator(sel)
                        cnt = candidates.count()
                        if cnt > 0:
                            for idx in range(cnt):
                                cand = candidates.nth(idx)
                                try:
                                    if cand.evaluate("el => !!el.closest('#msg-overlay')"):
                                        continue
                                except Exception:
                                    pass
                                like_btn = cand
                                break
                        if like_btn:
                            break
                    if like_btn:
                        break
                    # Scroll down progressively to trigger lazy hydration
                    try:
                        page.mouse.wheel(0, 500)
                        human_sleep(1.0, 1.5)
                    except Exception:
                        pass

                if like_btn:
                    try:
                        like_btn.scroll_into_view_if_needed(timeout=5000)
                        human_sleep(0.8, 1.5)
                    except Exception as err_scroll:
                        logger.debug("scroll_into_view_if_needed warning: %s", err_scroll)

                if not like_btn or not like_btn.is_visible():
                    shot_err = str(settings.downloads_path / f"like_not_found_{int(time.time())}.png")
                    page.screenshot(path=shot_err)
                    raise RuntimeError(f"Like button not found on {post_url}. Screenshot saved: {shot_err}")

                # Check if already liked
                aria_pressed = (like_btn.get_attribute("aria-pressed") or "").lower()
                btn_class = like_btn.get_attribute("class") or ""
                btn_label = (like_btn.get_attribute("aria-label") or "").lower()
                btn_text = (like_btn.inner_text() or "").lower()

                is_already_liked = False
                if "botão de reação" in btn_label or "reaction button" in btn_label:
                    # In pt-BR: "Situação do botão de reação: nenhuma reação" vs "Situação do botão de reação: Gostei"
                    # In en: "Reaction button state: no reaction" vs "Reaction button state: Like"
                    if not any(k in btn_label for k in ["nenhuma", "sem reação", "no reaction"]):
                        is_already_liked = True
                elif (
                    aria_pressed == "true"
                    or "react-button--active" in btn_class
                    or "desfazer" in btn_label
                    or "desfazer" in btn_text
                ):
                    is_already_liked = True

                if is_already_liked:
                    logger.info("Post %s is already liked. Skipping click.", post_url)
                    return {
                        "status": "ALREADY_LIKED",
                        "post_url": post_url,
                        "liked_at": datetime.now().isoformat(),
                    }

                logger.info("Clicking like button on %s...", post_url)
                human_click(page, like_btn)
                human_sleep(2.0, 3.5)

                shot_success = str(settings.downloads_path / f"like_success_{int(time.time())}.png")
                page.screenshot(path=shot_success)
                self.save_session_to_disk_and_redis(context)

                logger.info("LinkedIn post successfully liked! Screenshot: %s", shot_success)
                return {
                    "status": "SUCCESS",
                    "post_url": post_url,
                    "liked_at": datetime.now().isoformat(),
                    "screenshot": shot_success,
                }
            except Exception as e:
                logger.error("Failed to like LinkedIn post %s: %s", post_url, e)
                raise
            finally:
                context.close()
                if browser:
                    browser.close()

    def extract_prospects_from_post(self, post_url: str, limit: int = 15) -> List[Dict[str, str]]:
        """
        Navigates to a post, scrolls to load comments and engagements,
        and extracts profile links and headlines of active engagers.
        """
        logger.info("Extracting engager prospects from post: %s (limit=%d)", post_url, limit)
        state_file = self.sync_session_from_redis_or_disk()

        with sync_playwright() as p:
            browser, context, page = self._create_resilient_context(p, state_file=state_file)

            try:
                self.ensure_authenticated(page, context)
                page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
                human_sleep(2.5, 4.0)

                # Scroll down to load comments
                try:
                    human_scroll(page, steps=random.randint(2, 4), min_distance=200, max_distance=400)
                except Exception:
                    pass

                # Extract all profile links from post
                links = page.locator('a[href*="/in/"]').all()
                prospects = []
                seen_urls = set()

                for l in links:
                    try:
                        href = l.get_attribute("href") or ""
                        clean_url = href.split("?")[0].rstrip("/")
                        if not clean_url or "nogueira-fernando" in clean_url:
                            continue
                        if clean_url in seen_urls:
                            continue

                        # Extract name
                        name = l.inner_text().strip()
                        # Extract parent container text as potential headline context
                        parent_text = ""
                        try:
                            parent_text = l.evaluate("el => el.parentElement.parentElement ? el.parentElement.parentElement.innerText : ''")
                        except Exception:
                            pass

                        headline = ""
                        if parent_text:
                            lines = [ln.strip() for ln in parent_text.split("\n") if ln.strip() and ln.strip() != name]
                            if lines:
                                headline = lines[0]

                        if name and len(name) > 2:
                            seen_urls.add(clean_url)
                            prospects.append({
                                "profile_url": clean_url,
                                "nome": name,
                                "headline": headline[:150],
                            })
                            if len(prospects) >= limit:
                                break
                    except Exception:
                        continue

                logger.info("Discovered %d prospects on post %s", len(prospects), post_url)
                return prospects

            except Exception as e:
                logger.error("Failed to extract prospects from post %s: %s", post_url, e)
                return []
            finally:
                context.close()
                if browser:
                    browser.close()

    def send_connection_invite(self, profile_url: str, note_text: Optional[str] = None) -> Dict[str, Any]:
        """
        Navigates to a prospect profile and sends a humanized connection invitation
        with optional personalized note.
        """
        logger.info("Initiating connection invite to: %s (with_note=%s)", profile_url, bool(note_text))
        state_file = self.sync_session_from_redis_or_disk()

        with sync_playwright() as p:
            browser, context, page = self._create_resilient_context(p, state_file=state_file)

            try:
                self.ensure_authenticated(page, context)
                page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
                human_sleep(3.0, 5.0)

                top_card = page.locator("main section").first

                # 1. Check if already connected (1º grau)
                top_text = top_card.inner_text() if top_card.count() > 0 else ""
                if "• 1º" in top_text or "1st" in top_text:
                    logger.info("Profile %s is already a 1st degree connection. Skipping.", profile_url)
                    return {"status": "ALREADY_CONNECTED", "profile_url": profile_url}

                # 2. Check if already pending
                pending_btn = top_card.locator("button:has-text('Pendente'), button:has-text('Pending')").first
                if pending_btn.count() > 0 and pending_btn.is_visible():
                    logger.info("Invitation to %s is already pending. Skipping.", profile_url)
                    return {"status": "ALREADY_PENDING", "profile_url": profile_url}

                # 3. Locate connection trigger
                # Try direct connect button on top card
                direct_conn = top_card.locator('a[href*="custom-invite"], button[aria-label*="para se conectar"], button:has-text("Conectar"), button:has-text("Connect")').first
                triggered = False

                if direct_conn.count() > 0 and direct_conn.is_visible():
                    logger.info("Found direct connect button. Triggering...")
                    try:
                        direct_conn.dispatch_event("click")
                        triggered = True
                    except Exception:
                        direct_conn.click(force=True)
                        triggered = True

                # If direct button not found or creator profile, check "Mais" dropdown
                if not triggered:
                    mais_btn = top_card.locator('button:has-text("Mais"), button[aria-label*="Mais ações"], button:has-text("More")').first
                    if mais_btn.count() > 0 and mais_btn.is_visible():
                        logger.info("Opening 'Mais' menu to find Conectar option...")
                        mais_btn.click()
                        human_sleep(1.0, 1.8)

                        conn_menu_item = page.locator('div[role="menu"] a[href*="custom-invite"], div[role="menu"] [componentkey*="ConnectButton"], div[role="menu"] p:has-text("Conectar")').first
                        if conn_menu_item.count() > 0 and conn_menu_item.is_visible():
                            logger.info("Found Conectar item in menu. Triggering...")
                            try:
                                conn_menu_item.dispatch_event("click")
                                triggered = True
                            except Exception:
                                conn_menu_item.click(force=True)
                                triggered = True

                if not triggered:
                    logger.warning("No connect button or menu item available for %s.", profile_url)
                    return {"status": "NO_CONNECT_OPTION", "profile_url": profile_url}

                human_sleep(2.0, 3.5)

                # 4. Handle connection dialog
                dialog = page.locator('dialog, div[role="dialog"]').filter(has_text="Adicionar nota").first
                if not dialog.is_visible(timeout=6000):
                    dialog = page.locator('dialog, div[role="dialog"], .artdeco-modal').first

                if dialog.count() > 0 and dialog.is_visible():
                    if note_text and note_text.strip():
                        add_note_btn = dialog.locator('button:has-text("Adicionar nota"), button:has-text("Add a note")').first
                        if add_note_btn.count() > 0 and add_note_btn.is_visible():
                            logger.info("Adding personalized invitation note...")
                            add_note_btn.click()
                            human_sleep(1.0, 2.0)

                            textarea = dialog.locator('textarea, div[role="textbox"]').first
                            if textarea.count() > 0 and textarea.is_visible():
                                human_type(page, textarea, note_text.strip()[:290], min_delay_ms=25, max_delay_ms=65)
                                human_sleep(1.5, 2.5)

                        send_btn = dialog.locator('button:has-text("Enviar"), button:has-text("Send")').last
                        if send_btn.count() > 0 and send_btn.is_visible():
                            send_btn.click()
                            logger.info("Clicked Enviar with note.")
                    else:
                        send_no_note = dialog.locator('button:has-text("Enviar sem nota"), button:has-text("Send without a note"), button:has-text("Enviar")').first
                        if send_no_note.count() > 0 and send_no_note.is_visible():
                            send_no_note.click()
                            logger.info("Clicked Enviar sem nota.")

                human_sleep(3.0, 5.0)
                self.save_session_to_disk_and_redis(context)

                logger.info("Connection invite successfully sent to %s!", profile_url)
                return {
                    "status": "INVITED",
                    "profile_url": profile_url,
                    "with_note": bool(note_text),
                    "sent_at": datetime.now().isoformat(),
                }

            except Exception as e:
                logger.error("Failed to send connection invite to %s: %s", profile_url, e, exc_info=True)
                return {"status": "FAILED", "profile_url": profile_url, "error": str(e)}
            finally:
                context.close()
                if browser:
                    browser.close()


linkedin_publisher = LinkedInPublisher()


