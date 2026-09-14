"""Scrapers package containing browser abstraction and anti-detection."""
from scrapers.base import BasePlaywrightScraper
from scrapers.quote_scraper import QuotesScraper
from scrapers.stealth import STEALTH_EVASION_SCRIPT
from scrapers.humanizer import human_type, human_scroll, human_sleep, human_click

__all__ = [
    "BasePlaywrightScraper",
    "QuotesScraper",
    "STEALTH_EVASION_SCRIPT",
    "human_type",
    "human_scroll",
    "human_sleep",
    "human_click",
]
