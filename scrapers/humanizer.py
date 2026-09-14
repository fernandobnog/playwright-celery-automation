"""
Human interaction simulator for Playwright.
Simulates typing pauses, randomized mouse paths, natural scrolling, and organic jitter.
"""

import random
import time
from playwright.sync_api import Page


def human_sleep(min_sec: float = 0.5, max_sec: float = 1.8) -> None:
    """Sleeps for a random duration with slight jitter."""
    duration = random.uniform(min_sec, max_sec)
    time.sleep(duration)


def human_type(
    page: Page,
    selector: str,
    text: str,
    min_delay_ms: int = 40,
    max_delay_ms: int = 150,
) -> None:
    """
    Types text into an input field character by character with randomized delays,
    mimicking natural human cadence and occasional micro-pauses.
    """
    element = page.locator(selector)
    element.click()
    human_sleep(0.2, 0.5)

    for char in text:
        page.keyboard.press(char)
        # Random typing delay between keystrokes
        delay_seconds = random.uniform(min_delay_ms / 1000.0, max_delay_ms / 1000.0)
        
        # 5% chance of a micro-pause (thinking pause)
        if random.random() < 0.05:
            delay_seconds += random.uniform(0.2, 0.6)
            
        time.sleep(delay_seconds)

    human_sleep(0.3, 0.7)


def human_scroll(
    page: Page,
    steps: int = 4,
    min_distance: int = 150,
    max_distance: int = 400,
) -> None:
    """
    Scrolls the page smoothly in random steps, simulating human reading patterns.
    """
    for _ in range(steps):
        distance = random.randint(min_distance, max_distance)
        # Mouse wheel scroll
        page.mouse.wheel(0, distance)
        # Pause to simulate reading content
        human_sleep(0.4, 1.2)


def human_click(page: Page, selector: str) -> None:
    """
    Moves mouse with slight offset and clicks element naturally.
    """
    locator = page.locator(selector)
    box = locator.bounding_box()
    if box:
        # Click slightly off-center for realism
        offset_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
        offset_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
        page.mouse.move(offset_x, offset_y, steps=random.randint(5, 12))
        human_sleep(0.1, 0.3)
        page.mouse.down()
        human_sleep(0.05, 0.15)
        page.mouse.up()
    else:
        locator.click()
    human_sleep(0.3, 0.8)
