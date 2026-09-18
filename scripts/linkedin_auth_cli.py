"""
CLI script to authenticate with LinkedIn and persist shared storage_state across all workers.
Can be run inside worker container to perform automated login or supply PIN/2FA if prompted.
"""

import argparse
import logging
from pathlib import Path
import time
from playwright.sync_api import sync_playwright
from redis import Redis

from core.config import settings
from scrapers.humanizer import human_sleep
from scrapers.linkedin_publisher import REDIS_AUTH_KEY, linkedin_publisher
from scrapers.stealth import STEALTH_EVASION_SCRIPT, get_random_user_agent, get_random_viewport

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("linkedin_auth")


def authenticate_linkedin(pin: str = None) -> bool:
    username = settings.LINKEDIN_USERNAME
    password = settings.LINKEDIN_PASSWORD
    state_path = Path(settings.AUTH_STATE_DIR) / "linkedin_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)

    if not username or not password:
        logger.error("LINKEDIN_USERNAME or LINKEDIN_PASSWORD not set in environment.")
        return False

    logger.info("Launching Playwright Chromium to authenticate LinkedIn for %s...", username)

    with sync_playwright() as p:
        viewport = get_random_viewport()
        user_agent = get_random_user_agent()

        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )

        context_kwargs = {
            "viewport": viewport,
            "user_agent": user_agent,
            "locale": "pt-BR",
            "timezone_id": "America/Sao_Paulo",
        }
        if state_path.exists():
            context_kwargs["storage_state"] = str(state_path)

        context = browser.new_context(**context_kwargs)
        context.add_init_script(STEALTH_EVASION_SCRIPT)
        page = context.new_page()

        try:
            # 1. Check if already logged in
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
            human_sleep(2.0, 3.5)

            if linkedin_publisher.is_logged_in(page):
                logger.info("SUCCESS: Already authenticated on LinkedIn!")
                linkedin_publisher.save_session_to_disk_and_redis(context)
                return True

            # 2. If PIN was provided for 2FA/challenge
            if pin:
                logger.info("Attempting to enter supplied 2FA PIN: %s", pin)
                pin_inputs = [
                    "input#input__email_verification_pin",
                    "input[name='pin']",
                    "input[type='tel']",
                    "input[name='email-pin']",
                ]
                for p_sel in pin_inputs:
                    loc = page.locator(p_sel).first
                    if loc.is_visible(timeout=3000):
                        loc.fill(pin.strip())
                        human_sleep(0.5, 1.0)
                        submit_btn = page.locator("button[type='submit'], button#email-pin-submit-button").first
                        if submit_btn.is_visible():
                            submit_btn.click()
                        page.wait_for_load_state("domcontentloaded", timeout=20000)
                        human_sleep(3.0, 5.0)
                        break

                if linkedin_publisher.is_logged_in(page):
                    logger.info("SUCCESS: Authenticated via 2FA PIN!")
                    linkedin_publisher.save_session_to_disk_and_redis(context)
                    return True

            # 3. Perform login
            logger.info("Navigating to https://www.linkedin.com/login...")
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            human_sleep(1.5, 2.5)

            # Fill username
            user_input = page.locator("input[type='email']:visible, input[name='session_key']:visible, input#username:visible").first
            if user_input.is_visible(timeout=5000):
                user_input.click()
                user_input.fill(username)
                human_sleep(0.4, 0.8)

            # Fill password
            pass_input = page.locator("input[type='password']:visible, input[name='session_password']:visible, input#password:visible").first
            if pass_input.is_visible(timeout=5000):
                pass_input.click()
                pass_input.fill(password)
                human_sleep(0.4, 0.8)

            # Submit
            submit_btn = page.locator("button:has-text('Entrar'):visible, button:has-text('Sign in'):visible, button[type='submit']:visible").first
            if submit_btn.is_visible(timeout=5000):
                submit_btn.click()

            page.wait_for_load_state("domcontentloaded", timeout=25000)
            human_sleep(4.0, 6.0)

            current_url = page.url
            logger.info("Current URL after login attempt: %s", current_url)

            # Take diagnostic screenshot
            shot_file = settings.downloads_path / "linkedin_login_result.png"
            page.screenshot(path=str(shot_file))
            logger.info("Screenshot saved to %s", shot_file)

            if linkedin_publisher.is_logged_in(page):
                logger.info("SUCCESS: Authenticated successfully! Saving storage_state...")
                linkedin_publisher.save_session_to_disk_and_redis(context)
                return True

            # Check if 2FA or security challenge is active
            if "checkpoint" in current_url or "challenge" in current_url:
                header_text = ""
                try:
                    header_text = page.locator("h1, .header__content__heading").first.inner_text(timeout=2000)
                except Exception:
                    pass
                logger.warning("LinkedIn requires 2FA or security verification: '%s' at URL: %s", header_text, current_url)
                print(f"CHECKPOINT_REQUIRED: {header_text or current_url}")
                return False

            logger.error("Login failed. Check screenshot: %s", shot_file)
            return False

        finally:
            context.close()
            browser.close()


def authenticate_with_cookie(li_at_cookie: str) -> bool:
    import json
    clean_val = li_at_cookie.strip().strip('"').strip("'")
    if not clean_val:
        logger.error("Cookie value cannot be empty.")
        return False

    state_path = Path(settings.AUTH_STATE_DIR) / "linkedin_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)

    dummy_state = {
        "cookies": [
            {
                "name": "li_at",
                "value": clean_val,
                "domain": ".www.linkedin.com",
                "path": "/",
                "expires": int(time.time()) + 31536000,
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            },
            {
                "name": "li_at",
                "value": clean_val,
                "domain": ".linkedin.com",
                "path": "/",
                "expires": int(time.time()) + 31536000,
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            },
        ],
        "origins": [],
    }
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(dummy_state, f)

    logger.info("Testing cookie authentication with Playwright...")
    with sync_playwright() as p:
        viewport = get_random_viewport()
        user_agent = get_random_user_agent()
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            viewport=viewport,
            user_agent=user_agent,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            storage_state=str(state_path),
        )
        context.add_init_script(STEALTH_EVASION_SCRIPT)
        page = context.new_page()
        try:
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
            human_sleep(2.0, 4.0)
            if linkedin_publisher.is_logged_in(page):
                logger.info("🎉 SUCCESS: Valid LinkedIn session restored via cookie! Syncing across all workers...")
                linkedin_publisher.save_session_to_disk_and_redis(context)
                return True
            else:
                logger.error("Cookie provided is invalid or expired. Current URL: %s", page.url)
                return False
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pin", type=str, help="2FA PIN sent by LinkedIn (if required)")
    parser.add_argument("--cookie", type=str, help="Raw li_at session cookie from your browser")
    args = parser.parse_args()

    if args.cookie:
        success = authenticate_with_cookie(li_at_cookie=args.cookie)
    else:
        success = authenticate_linkedin(pin=args.pin)
    exit(0 if success else 1)

