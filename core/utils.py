"""
General utilities for URL normalization and sanitization.
"""

import re


def normalize_url(url: str) -> str:
    """
    Normalizes a URL string:
    - Strips leading/trailing whitespace.
    - Handles collapsed slashes (e.g. 'https:/example.com' -> 'https://example.com').
    - If no scheme is provided (e.g. 'example.com', 'sub.domain.com/path'),
      automatically prepends 'https://'.
    """
    if not url:
        return ""
    
    url = url.strip()

    # Fix collapsed slashes from URL path parameters or proxies
    if url.startswith("https:/") and not url.startswith("https://"):
        url = "https://" + url[7:].lstrip("/")
    elif url.startswith("http:/") and not url.startswith("http://"):
        url = "http://" + url[6:].lstrip("/")

    # If scheme is completely missing, default to https://
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    return url
