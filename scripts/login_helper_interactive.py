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
from scrapers.linkedin_publisher import linkedin_publisher
from scrapers.stealth import STEALTH_EVASION_SCRIPT, get_random_user_agent, get_random_viewport

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("interactive_login")


def run_interactive_login(timeout_seconds: int = 600):
    username = settings.LINKEDIN_USERNAME
    password = settings.LINKEDIN_PASSWORD
    if not username or not password:
        logger.error("LINKEDIN_USERNAME or LINKEDIN_PASSWORD not configured in settings/env.")
        return False
    state_path = Path(settings.AUTH_STATE_DIR) / "linkedin_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Starting interactive login helper on display :99 (viewable at noVNC port 6081)...")

    with sync_playwright() as p:
        viewport = get_random_viewport()
        user_agent = get_random_user_agent()

        browser = p.chromium.launch(
            headless=False,  # Visible on Xvfb :99 / noVNC
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = browser.new_context(
            viewport=viewport,
            user_agent=user_agent,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )
        context.add_init_script(STEALTH_EVASION_SCRIPT)
        page = context.new_page()

        try:
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
                pass_loc.press("Enter")

            logger.info("Credentials submitted. Waiting for verification/feed on noVNC (port 6081)...")
            logger.info("Open http://localhost:6081/vnc.html in your browser to solve any security challenge.")

            start_time = time.time()
            last_report = 0
            while time.time() - start_time < timeout_seconds:
                if linkedin_publisher.is_logged_in(page):
                    logger.info("🎉 SUCCESS: LinkedIn login confirmed and active!")
                    linkedin_publisher.save_session_to_disk_and_redis(context)
                    print("LINKEDIN_LOGIN_SUCCESS")
                    return True

                elapsed = int(time.time() - start_time)
                if elapsed - last_report >= 30:
                    logger.info("Awaiting login on noVNC... (%d/%d seconds elapsed). Current URL: %s", elapsed, timeout_seconds, page.url)
                    last_report = elapsed

                time.sleep(2.0)

            logger.warning("Interactive login timed out after %d seconds.", timeout_seconds)
            return False
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    run_interactive_login()
