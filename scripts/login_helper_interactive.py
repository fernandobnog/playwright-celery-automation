"""
Interactive LinkedIn Login Helper.
Fills credentials and leaves Chromium open on display :99 (streamed to noVNC port 6081).
Allows the user to solve the one-time reCAPTCHA on http://localhost:6081/vnc.html.
As soon as the feed is reached, automatically saves storage_state to shared disk and Redis.
"""

import logging
from pathlib import Path
import time
from playwright.sync_api import sync_playwright

from core.config import settings
from scrapers.humanizer import human_sleep
from scrapers.linkedin_publisher import linkedin_publisher, PERSISTENT_USER_AGENT
from scrapers.stealth import STEALTH_EVASION_SCRIPT, get_random_viewport

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("interactive_login")


def run_interactive_login(timeout_seconds: int = 7200):
    username = settings.LINKEDIN_USERNAME
    password = settings.LINKEDIN_PASSWORD
    if not username or not password:
        logger.error("LINKEDIN_USERNAME or LINKEDIN_PASSWORD not configured in settings/env.")
        return False
    state_path = Path(settings.AUTH_STATE_DIR) / "linkedin_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    profile_dir = settings.PLAYWRIGHT_USER_DATA_DIR or "/app/data/browser_profile"

    logger.info("Starting interactive login helper with persistent profile at %s on display :99 (noVNC port 6081)...", profile_dir)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,  # Visible on Xvfb :99 / noVNC
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            viewport={"width": 1280, "height": 850},
            user_agent=PERSISTENT_USER_AGENT,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )
        context.add_init_script(STEALTH_EVASION_SCRIPT)
        page = context.pages[0] if context.pages else context.new_page()

        try:
            logger.info("Checking if persistent profile is already logged in on feed...")
            try:
                page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=25000)
                human_sleep(2.0, 3.5)
                if linkedin_publisher.is_logged_in(page):
                    logger.info("🎉 SUCCESS: Session already valid and verified on persistent profile!")
                    linkedin_publisher.save_session_to_disk_and_redis(context)
                    print("LINKEDIN_LOGIN_SUCCESS")
                    return True
            except Exception as e_check:
                logger.warning("Session pre-check notice: %s", e_check)

            logger.info("Navigating to https://www.linkedin.com/login...")
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            human_sleep(1.5, 2.5)

            # Fill credentials
            user_loc = page.locator("input[type='email']:visible, input[name='session_key']:visible").first
            if user_loc.is_visible(timeout=5000):
                user_loc.click()
                user_loc.fill(username)
                human_sleep(0.5, 1.0)

            pass_loc = page.locator("input[type='password']:visible, input[name='session_password']:visible").first
            if pass_loc.is_visible(timeout=5000):
                pass_loc.click()
                pass_loc.fill(password)
                human_sleep(0.5, 1.0)
                submit_loc = page.get_by_role("button", name="Entrar", exact=True)
                if not submit_loc.is_visible():
                    submit_loc = page.get_by_role("button", name="Sign in", exact=True)
                if not submit_loc.is_visible():
                    submit_loc = page.locator("button[type='submit']:visible, button.btn__primary--large:visible").first
                if submit_loc.is_visible(timeout=3000):
                    submit_loc.click()
                else:
                    pass_loc.press("Enter")

            logger.info("Credentials submitted. Waiting for verification/feed on noVNC (port 6081)...")
            logger.info("Open http://localhost:6081/vnc.html in your browser to solve any security challenge.")

            start_time = time.time()
            last_report = 0
            while time.time() - start_time < timeout_seconds:
                # 1. Check all open pages/tabs in the browser context
                for p in context.pages:
                    try:
                        if linkedin_publisher.is_logged_in(p):
                            logger.info("🎉 SUCCESS: LinkedIn login confirmed on tab (%s)!", p.url)
                            linkedin_publisher.save_session_to_disk_and_redis(context)
                            print("LINKEDIN_LOGIN_SUCCESS")
                            return True
                    except Exception:
                        pass

                # 2. Check if li_at cookie has already been set in browser context
                try:
                    cookies = context.cookies(["https://www.linkedin.com"])
                    if any(c.get("name") == "li_at" and len(c.get("value", "")) > 10 for c in cookies):
                        logger.info("Detected active 'li_at' cookie in browser! Navigating to feed to confirm...")
                        active_p = context.pages[-1] if context.pages else page
                        if "feed" not in active_p.url.lower():
                            active_p.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=15000)
                            human_sleep(2.0, 3.0)
                        if linkedin_publisher.is_logged_in(active_p):
                            logger.info("🎉 SUCCESS: LinkedIn feed reached and confirmed via active cookie!")
                            linkedin_publisher.save_session_to_disk_and_redis(context)
                            print("LINKEDIN_LOGIN_SUCCESS")
                            return True
                except Exception as e:
                    logger.debug("Cookie check exception: %s", e)

                elapsed = int(time.time() - start_time)
                if elapsed - last_report >= 15:
                    tab_urls = [p.url for p in context.pages]
                    logger.info("Awaiting login on noVNC... (%d/%d seconds elapsed). Open tabs: %s", elapsed, timeout_seconds, tab_urls)
                    try:
                        shot_path = Path("/app/downloads/interactive_login_status.png")
                        shot_path.parent.mkdir(parents=True, exist_ok=True)
                        (context.pages[-1] if context.pages else page).screenshot(path=str(shot_path))
                    except Exception:
                        pass
                    last_report = elapsed

                time.sleep(2.0)

            logger.warning("Interactive login timed out after %d seconds.", timeout_seconds)
            return False
        finally:
            try:
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    run_interactive_login()
