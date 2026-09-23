"""
Human interaction simulator for Playwright.
Simulates organic typing pauses, stochastic keystroke frequencies, cubic Bézier
mouse curves with physiological acceleration/jitter, and natural screen transitions.
"""

import math
import random
import time
from typing import Any, Optional, Union
from playwright.sync_api import Locator, Page


# Cache last known mouse position across calls to ensure continuous curves
_LAST_MOUSE_POS = {"x": 200.0, "y": 200.0}


def human_sleep(min_sec: float = 0.5, max_sec: float = 1.8) -> None:
    """Sleeps for a duration distributed around a normal bell curve rather than flat uniform."""
    mean = (min_sec + max_sec) / 2.0
    std = max(0.05, (max_sec - min_sec) / 4.0)
    duration = max(min_sec, min(max_sec, random.gauss(mean, std)))
    time.sleep(duration)


def _bezier_coord(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    """Calculates coordinate along a cubic Bézier curve at point t (0.0 to 1.0)."""
    return (
        ((1 - t) ** 3) * p0
        + 3 * ((1 - t) ** 2) * t * p1
        + 3 * (1 - t) * (t ** 2) * p2
        + (t ** 3) * p3
    )


def human_move_mouse(
    page: Page,
    target_x: float,
    target_y: float,
    steps: Optional[int] = None,
) -> None:
    """
    Moves mouse from current position to target coordinates using a cubic Bézier curve,
    incorporating Ease-In-Out acceleration and physiological micro-tremors (jitter).
    """
    global _LAST_MOUSE_POS
    start_x = _LAST_MOUSE_POS["x"]
    start_y = _LAST_MOUSE_POS["y"]

    dx = target_x - start_x
    dy = target_y - start_y
    distance = math.hypot(dx, dy)

    if distance < 4:
        page.mouse.move(target_x, target_y)
        _LAST_MOUSE_POS["x"], _LAST_MOUSE_POS["y"] = target_x, target_y
        return

    # Angular deviation to create organic curves instead of straight lines
    angle = math.atan2(dy, dx)
    deviation = distance * random.uniform(0.12, 0.32)
    sign = 1 if random.random() > 0.5 else -1
    normal_angle = angle + sign * (math.pi / 2)

    ctrl1_x = start_x + (distance * random.uniform(0.25, 0.40)) * math.cos(angle) + deviation * math.cos(normal_angle)
    ctrl1_y = start_y + (distance * random.uniform(0.25, 0.40)) * math.sin(angle) + deviation * math.sin(normal_angle)

    ctrl2_x = start_x + (distance * random.uniform(0.60, 0.80)) * math.cos(angle) + (deviation * random.uniform(0.3, 0.7)) * math.cos(normal_angle)
    ctrl2_y = start_y + (distance * random.uniform(0.60, 0.80)) * math.sin(angle) + (deviation * random.uniform(0.3, 0.7)) * math.sin(normal_angle)

    # Number of steps proportional to distance: slower, smoother movement
    num_steps = steps or max(35, int(distance / random.uniform(6.0, 9.5)))

    for i in range(1, num_steps + 1):
        raw_t = i / num_steps
        # Ease-In-Out sinusoidal curve (starts slow, accelerates in middle, decelerates on arrival)
        t = (1 - math.cos(raw_t * math.pi)) / 2

        x = _bezier_coord(start_x, ctrl1_x, ctrl2_x, target_x, t)
        y = _bezier_coord(start_y, ctrl1_y, ctrl2_y, target_y, t)

        # Micro-tremor / neuromuscular jitter
        jitter_x = x + random.gauss(0, 0.45)
        jitter_y = y + random.gauss(0, 0.45)

        page.mouse.move(jitter_x, jitter_y)
        # Small non-uniform interval between steps
        time.sleep(random.uniform(0.009, 0.022))

    # Exact final placement
    page.mouse.move(target_x, target_y)
    _LAST_MOUSE_POS["x"], _LAST_MOUSE_POS["y"] = target_x, target_y
    time.sleep(random.uniform(0.12, 0.35))


def human_idle_wander(page: Page, duration_sec: float = 2.0) -> None:
    """
    Simulates human reading or thinking by gently drifting the mouse cursor
    in small micro-arcs rather than remaining completely frozen on screen.
    """
    global _LAST_MOUSE_POS
    end_time = time.time() + duration_sec
    while time.time() < end_time:
        drift_x = _LAST_MOUSE_POS["x"] + random.uniform(-40, 40)
        drift_y = _LAST_MOUSE_POS["y"] + random.uniform(-30, 30)
        drift_x = max(10, min(1200, drift_x))
        drift_y = max(10, min(800, drift_y))
        human_move_mouse(page, drift_x, drift_y, steps=random.randint(20, 35))
        time.sleep(random.uniform(0.4, 0.9))


def human_click(page: Page, selector_or_locator: Union[str, Locator, Any]) -> None:
    """
    Moves mouse with cubic Bézier trajectory to a decentralized random point
    inside the target element, pauses organically, and performs physical down/up click.
    """
    if isinstance(selector_or_locator, str):
        locator = page.locator(selector_or_locator).first
    else:
        locator = selector_or_locator

    try:
        box = locator.bounding_box()
    except Exception:
        box = None

    if box:
        # Click randomly inside the inner 25% - 75% boundary of the target element
        target_x = box["x"] + box["width"] * random.uniform(0.25, 0.75)
        target_y = box["y"] + box["height"] * random.uniform(0.25, 0.75)

        human_move_mouse(page, target_x, target_y)

        # Pre-click hesitation (reaction time)
        time.sleep(random.uniform(0.18, 0.45))

        # Physical click duration (realistic switch contact duration)
        page.mouse.down()
        time.sleep(random.uniform(0.07, 0.16))
        page.mouse.up()
    else:
        # Fallback if bounding box is not accessible
        locator.click()

    # Post-click pause
    human_sleep(0.35, 0.85)


def human_type(
    page: Page,
    selector_or_locator: Union[str, Locator, Any],
    text: str,
    min_delay_ms: int = 70,
    max_delay_ms: int = 240,
) -> None:
    """
    Types text into an input field or contenteditable element with organic, non-uniform
    keystroke frequencies, contextual punctuation pauses, and cognitive hesitation intervals.
    """
    # 1. Physically click the element to acquire focus
    human_click(page, selector_or_locator)
    human_sleep(0.4, 0.9)

    chars_since_pause = 0
    pause_threshold = random.randint(16, 32)

    for char in text:
        # Keystroke delay from a normal distribution representing human motor variance
        mean_delay = (min_delay_ms + max_delay_ms) / 2000.0
        std_delay = (max_delay_ms - min_delay_ms) / 6000.0
        delay = max(0.045, min(0.38, random.gauss(mean_delay, std_delay)))

        page.keyboard.type(char, delay=int(delay * 1000))
        chars_since_pause += 1

        # Cognitive pauses for punctuation and paragraph transitions
        if char in [".", "!", "?", "\n"]:
            time.sleep(random.uniform(0.8, 2.2))
        elif char in [",", ";", ":", "-", "—"]:
            time.sleep(random.uniform(0.35, 0.80))
        elif char == " ":
            time.sleep(random.uniform(0.10, 0.28))

        # Occasional hesitation / re-reading pause simulating thoughtful writing
        if chars_since_pause >= pause_threshold:
            time.sleep(random.uniform(1.4, 3.4))
            chars_since_pause = 0
            pause_threshold = random.randint(18, 36)

    human_sleep(0.8, 1.8)


def human_scroll(
    page: Page,
    steps: int = 4,
    min_distance: int = 120,
    max_distance: int = 350,
) -> None:
    """
    Scrolls the viewport smoothly in fractional increments with organic pauses,
    replicating natural reading and scanning behavior.
    """
    for _ in range(steps):
        distance = random.randint(min_distance, max_distance)
        # Fractional wheel increments
        fractional_steps = random.randint(4, 8)
        step_delta = distance / fractional_steps
        for _ in range(fractional_steps):
            page.mouse.wheel(0, step_delta)
            time.sleep(random.uniform(0.03, 0.08))
        # Reading pause after scroll
        human_sleep(0.6, 1.6)
