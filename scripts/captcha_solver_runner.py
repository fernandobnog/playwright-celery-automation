"""
Persistent Vision-Powered CAPTCHA Solver and Direct LinkedIn Publisher.
Uses /app/data/browser_profile to store persistent session and device keys.
Supports full vision loop for puzzles and immediate publication.
"""

import json
import logging
from pathlib import Path
import time
from typing import Optional

from playwright.sync_api import sync_playwright
from core.config import settings
from scrapers.humanizer import human_sleep
from scrapers.linkedin_publisher import linkedin_publisher
from scrapers.stealth import STEALTH_EVASION_SCRIPT

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("captcha_solver")

DOWNLOADS_DIR = Path("/app/downloads")
STATE_FILE = DOWNLOADS_DIR / "puzzle_state.json"
ACTION_FILE = DOWNLOADS_DIR / "puzzle_action.json"
FULL_SCREENSHOT = DOWNLOADS_DIR / "puzzle_screen.png"
BFRAME_SCREENSHOT = DOWNLOADS_DIR / "puzzle_bframe.png"
PROFILE_DIR = "/app/data/browser_profile"


def run_solver_and_publisher():
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    if ACTION_FILE.exists():
        ACTION_FILE.unlink()

    username = settings.LINKEDIN_USERNAME
    password = settings.LINKEDIN_PASSWORD
    if not username or not password:
        logger.error("Missing LINKEDIN_USERNAME or LINKEDIN_PASSWORD")
        return

    logger.info("Launching Persistent Chromium with profile at %s...", PROFILE_DIR)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            viewport={"width": 1280, "height": 850},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )
        context.add_init_script(STEALTH_EVASION_SCRIPT)
        page = context.pages[0] if context.pages else context.new_page()

        logger.info("Checking initial session on feed...")
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        time.sleep(3.0)

        if not linkedin_publisher.is_logged_in(page):
            logger.info("Not logged in. Navigating to login page...")
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            human_sleep(1.5, 2.5)

            user_inp = page.locator("input[type='email']:visible, input[name='session_key']:visible").first
            pass_inp = page.locator("input[type='password']:visible, input[name='session_password']:visible").first
            if user_inp.is_visible():
                user_inp.fill(username)
                human_sleep(0.3, 0.6)
            if pass_inp.is_visible():
                pass_inp.fill(password)
                human_sleep(0.3, 0.6)

            entrar_btn = page.get_by_role("button", name="Entrar", exact=True)
            if entrar_btn.is_visible():
                entrar_btn.click()
            elif pass_inp.is_visible():
                pass_inp.press("Enter")

            logger.info("Credentials submitted. Waiting for checkpoint or feed...")
            try:
                page.wait_for_url(lambda u: "checkpoint" in u or "feed" in u, timeout=25000)
            except Exception:
                logger.warning("Wait for URL timed out. Current URL: %s", page.url)

            time.sleep(3.0)

        anchor_clicked = False
        loop_count = 0

        while True:
            loop_count += 1

            # 1. Check if login succeeded
            if linkedin_publisher.is_logged_in(page):
                logger.info("🎉 SUCCESS! Logged in to LinkedIn with Persistent Profile!")
                linkedin_publisher.save_session_to_disk_and_redis(context)
                try:
                    page.screenshot(path=str(FULL_SCREENSHOT))
                except Exception:
                    pass
                STATE_FILE.write_text(json.dumps({"status": "LOGGED_IN", "message": "Logged in", "url": page.url}), encoding="utf-8")

            # 2. Expand #captcha-internal iframe
            try:
                page.evaluate("""() => {
                    const el = document.getElementById('captcha-internal');
                    if (el) {
                        el.style.height = '620px';
                        el.style.width = '420px';
                        el.style.overflow = 'visible';
                        el.style.zIndex = '99999';
                    }
                }""")
            except Exception:
                pass

            ci = page.frame_locator("#captcha-internal")

            # 3. Click reCAPTCHA anchor if not clicked
            if not anchor_clicked and "checkpoint" in page.url:
                try:
                    rc_anchor = ci.frame_locator('iframe[src*="anchor"][src*="size=normal"]').locator('#recaptcha-anchor, .recaptcha-checkbox').first
                    if rc_anchor.is_visible(timeout=2000):
                        logger.info("Clicking reCAPTCHA anchor checkbox...")
                        rc_anchor.click()
                        anchor_clicked = True
                        time.sleep(3.0)
                except Exception as e:
                    logger.debug("Anchor check: %s", e)

            # 4. Check bframe
            bframe = ci.frame_locator('iframe[src*="bframe"]')
            desc_loc = bframe.locator(".rc-imageselect-desc-wrapper, .rc-imageselect-instructions, strong").first
            is_challenge_visible = False
            instructions = ""
            tiles_count = 0

            try:
                if desc_loc.is_visible(timeout=1000):
                    is_challenge_visible = True
                    instructions = desc_loc.inner_text().replace("\n", " ").strip()
                    tiles = bframe.locator("td.rc-imageselect-tile").all()
                    tiles_count = len(tiles)
            except Exception:
                pass

            # 5. Capture screenshots
            try:
                page.screenshot(path=str(FULL_SCREENSHOT))
            except Exception:
                pass

            if is_challenge_visible:
                try:
                    bframe.locator(".rc-imageselect-challenge, body").first.screenshot(path=str(BFRAME_SCREENSHOT))
                except Exception:
                    try:
                        page.locator("#captcha-internal").first.screenshot(path=str(BFRAME_SCREENSHOT))
                    except Exception:
                        pass

            # 6. Write state
            curr_status = "LOGGED_IN" if linkedin_publisher.is_logged_in(page) else ("CHALLENGE_ACTIVE" if is_challenge_visible else "WAITING")
            state_data = {
                "status": curr_status,
                "instructions": instructions,
                "tiles_count": tiles_count,
                "url": page.url,
                "title": page.title(),
                "loop": loop_count,
                "timestamp": time.time(),
            }
            STATE_FILE.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
            logger.info("Loop %d | Status: %s | Instructions: '%s' | Tiles: %d", 
                        loop_count, state_data["status"], instructions[:40], tiles_count)

            # 7. Wait for action file
            action_executed = False
            for _ in range(20):
                if ACTION_FILE.exists():
                    try:
                        raw_action = ACTION_FILE.read_text(encoding="utf-8")
                        action_data = json.loads(raw_action)
                        ACTION_FILE.unlink()
                        logger.info("Executing action: %s", action_data)

                        action_name = action_data.get("action")
                        if action_name == "quit":
                            logger.info("Quit action received.")
                            return

                        if action_name in ["click_tile", "click_tiles"]:
                            indices = action_data.get("indices", [])
                            if "index" in action_data:
                                indices = [action_data["index"]]

                            tiles = bframe.locator("td.rc-imageselect-tile").all()
                            for idx in indices:
                                if 0 <= idx < len(tiles):
                                    logger.info("Clicking tile %d of %d...", idx, len(tiles))
                                    tiles[idx].click()
                                    human_sleep(0.4, 0.7)

                            if action_data.get("verify", False) or action_data.get("confirm", False):
                                time.sleep(1.0)
                                verify_btn = bframe.locator("#recaptcha-verify-button").first
                                if verify_btn.is_visible(timeout=2000):
                                    logger.info("Clicking verify button: %s", verify_btn.inner_text())
                                    verify_btn.click()
                                    time.sleep(2.5)

                        elif action_name in ["confirm", "verify"]:
                            verify_btn = bframe.locator("#recaptcha-verify-button").first
                            if verify_btn.is_visible(timeout=2000):
                                logger.info("Clicking verify button: %s", verify_btn.inner_text())
                                verify_btn.click()
                                time.sleep(2.5)

                        elif action_name == "reload":
                            reload_btn = bframe.locator("#recaptcha-reload-button").first
                            if reload_btn.is_visible(timeout=2000):
                                reload_btn.click()
                                time.sleep(2.0)

                        elif action_name == "reclick_anchor":
                            rc_anchor = ci.frame_locator('iframe[src*="anchor"][src*="size=normal"]').locator('#recaptcha-anchor, .recaptcha-checkbox').first
                            rc_anchor.click()
                            time.sleep(3.0)

                        elif action_name == "publish_post":
                            post_text = action_data.get("text", "")
                            logger.info("Direct publish requested from persistent context...")
                            if "feed" not in page.url:
                                page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
                                time.sleep(3.0)

                            # Find trigger
                            triggers = page.locator("button:has-text('Começar publicação'), button:has-text('Start a post'), button.share-box-feed-entry__trigger, div.share-box-feed-entry__top-bar").all()
                            trigger_found = None
                            for t in triggers:
                                if t.is_visible():
                                    trigger_found = t
                                    break

                            if trigger_found:
                                trigger_found.click()
                                time.sleep(2.0)

                            editor = page.locator("div[role='textbox'], div.editor-content div[contenteditable='true'], div.ql-editor").first
                            editor.wait_for(state="visible", timeout=10000)
                            editor.click()
                            human_sleep(0.5, 1.0)
                            page.keyboard.insert_text(post_text.strip())
                            human_sleep(1.0, 2.0)

                            submit_btn = page.locator("button.share-actions__primary-action, button:has-text('Publicar'), button:has-text('Post')").first
                            submit_btn.wait_for(state="visible", timeout=5000)
                            submit_btn.click()
                            time.sleep(5.0)

                            linkedin_publisher.save_session_to_disk_and_redis(context)
                            shot = str(DOWNLOADS_DIR / f"linkedin_published_{int(time.time())}.png")
                            page.screenshot(path=shot)
                            logger.info("Direct publication SUCCESSFUL! Screenshot: %s", shot)
                            STATE_FILE.write_text(json.dumps({"status": "PUBLISHED", "screenshot": shot}), encoding="utf-8")

                        elif action_name == "eval":
                            code = action_data.get("code", "")
                            exec(code, {
                                "page": page,
                                "context": context,
                                "bframe": bframe,
                                "ci": ci,
                                "time": time,
                                "logger": logger,
                                "human_sleep": human_sleep,
                                "linkedin_publisher": linkedin_publisher,
                            })

                        action_executed = True
                        break
                    except Exception as err:
                        logger.error("Error executing action: %s", err)

                time.sleep(0.5)

            if not action_executed:
                time.sleep(0.5)

        context.close()


if __name__ == "__main__":
    run_solver_and_publisher()
