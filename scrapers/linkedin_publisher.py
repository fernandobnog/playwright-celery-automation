"""
LinkedIn Autonomous Publisher (Playwright + Shared Storage State).
Enables zero-bureaucracy automated posting to LinkedIn Feed & Pulse using a persistent,
shared authenticated browser session across all Celery workers.
"""

from datetime import datetime
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Dict, Optional
import markdown
from playwright.sync_api import BrowserContext, Page, sync_playwright
from redis import Redis

from core.config import settings
from scrapers.humanizer import human_click, human_scroll, human_sleep
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
    Strips raw divider lines, repeated H1 titles, metadata lines, and formats
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
        if trimmed.startswith("⏱️") or trimmed.startswith("🖼️"):
            continue
        # Strip leading # Title and *Subtitle* if repeated at the top of the body
        if skip_header and (trimmed.startswith("# ") or trimmed.startswith("*")):
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

        # Check for intermediate 'We are signing you in' / 'Estamos dando acesso' screen transition
        for _ in range(8):
            if self.is_logged_in(page):
                break
            try:
                page_text = page.content().lower()
                if any(phrase in page_text for phrase in ["signing you in", "dando acesso", "estamos dando"]):
                    logger.info("Detected LinkedIn transition screen ('Estamos dando acesso'). Waiting for auto-redirect...")
                    human_sleep(2.5, 3.5)
            except Exception:
                break

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
                    user_agent=PERSISTENT_USER_AGENT,
                    locale="pt-BR",
                    timezone_id="America/Sao_Paulo",
                )
                page = context.pages[0] if context.pages else context.new_page()
            else:
                browser = p.chromium.launch(headless=self.headless, args=launch_args)
                context_kwargs = {
                    "viewport": {"width": 1280, "height": 850},
                    "user_agent": PERSISTENT_USER_AGENT,
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

                # Organic warm-up scroll on feed before opening post modal
                try:
                    human_scroll(page, steps=2, min_distance=80, max_distance=220)
                    page.mouse.wheel(0, -120)
                    human_sleep(1.0, 2.0)
                except Exception:
                    pass

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

                editor.click()
                human_sleep(0.5, 1.0)

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

                # Insert the text smoothly preserving line breaks and emojis
                page.keyboard.insert_text(clean_feed_text)
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
                            for _ in range(10):
                                if next_btn.is_visible():
                                    next_btn.click()
                                    human_sleep(1.5, 2.5)
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
                                btn.click()
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
        image_path: Optional[str] = None,
        blog_url: Optional[str] = None,
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

            browser = None
            if settings.PLAYWRIGHT_USER_DATA_DIR and Path(settings.PLAYWRIGHT_USER_DATA_DIR).exists():
                logger.info("Using persistent browser profile for Pulse from: %s", settings.PLAYWRIGHT_USER_DATA_DIR)
                context = p.chromium.launch_persistent_context(
                    user_data_dir=settings.PLAYWRIGHT_USER_DATA_DIR,
                    headless=self.headless,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
                    viewport=viewport,
                    user_agent=PERSISTENT_USER_AGENT,
                    locale="pt-BR",
                    timezone_id="America/Sao_Paulo",
                )
            else:
                browser = p.chromium.launch(
                    headless=self.headless,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
                )
                context_kwargs = {
                    "viewport": viewport,
                    "user_agent": PERSISTENT_USER_AGENT,
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

                # 1. Navigate to modern article editor
                page.goto("https://www.linkedin.com/article/new/", wait_until="domcontentloaded", timeout=self.timeout_ms)
                human_sleep(3.0, 4.5)

                # 2. Optional: Upload cover image
                if image_path and Path(image_path).exists():
                    try:
                        cover_btn = page.locator("button:has-text('Carregar do computador'), button:has-text('Upload from computer')").first
                        if cover_btn.is_visible(timeout=4000):
                            with page.expect_file_chooser(timeout=5000) as fc_info:
                                cover_btn.click()
                            file_chooser = fc_info.value
                            file_chooser.set_files(image_path)
                            human_sleep(2.5, 3.5)

                            # Click modal Avançar / Salvar
                            modal_apply = page.locator("div[role='dialog'] button:has-text('Avançar'), div.artdeco-modal button:has-text('Avançar')").first
                            if modal_apply.is_visible(timeout=4000):
                                modal_apply.click()
                                human_sleep(2.0, 3.0)
                                logger.info("Cover image attached to Pulse article: %s", image_path)
                    except Exception as err_cover:
                        logger.warning("Could not attach cover image to Pulse article (%s). Continuing with text...", err_cover)

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

                title_loc.click()
                human_sleep(0.5, 1.0)
                page.keyboard.insert_text(title.strip())
                human_sleep(1.0, 2.0)

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

                body_loc.click()
                human_sleep(0.5, 1.0)

                # Clear default paragraph
                page.keyboard.press("Control+A")
                human_sleep(0.2, 0.4)
                page.keyboard.press("Backspace")
                human_sleep(0.4, 0.8)

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

                next_btn.click()
                human_sleep(3.0, 5.0)

                # 6. In the post-sharing modal, add optional hook and click primary Publicar
                share_input = page.locator("div[role='dialog'] div[role='textbox'], div[role='dialog'] div.ProseMirror").first
                if share_input.is_visible(timeout=4000):
                    share_input.click()
                    share_hook = f"Compartilho meu novo artigo de liderança no LinkedIn: '{title.strip()}'. Leitura completa abaixo 👇"
                    page.keyboard.insert_text(share_hook)
                    human_sleep(1.0, 2.0)

                # Click primary action publish button (not the audience settings button)
                pub_btn_selectors = [
                    "button.share-actions__primary-action",
                    "div[role='dialog'] button.artdeco-button--primary:has-text('Publicar')",
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

                pub_btn.click()
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


linkedin_publisher = LinkedInPublisher()
