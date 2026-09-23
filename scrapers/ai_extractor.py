"""
AI-Optimized Web Content Extractor.
Extracts clean, structured Markdown, rich metadata, and token estimations from any webpage.
Features:
- Noise removal (ads, cookie notices, navigation bars, tracking scripts, footers)
- Dual engine: Fast HTTP or Stealth Playwright Chromium (for JS-rendered SPAs and anti-bot protection)
- Markdown table, code block, and semantic hierarchy preservation
- Link cleaning (strips utm tracking parameters)
- Token count estimation for LLM context planning
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from core.security import validate_url_for_ssrf
from core.utils import normalize_url
from scrapers.base import BasePlaywrightScraper
from scrapers.humanizer import human_scroll, human_sleep
from scrapers.stealth import get_random_user_agent

logger = logging.getLogger(__name__)

# Tracking query parameters to strip from hyperlinks for cleaner LLM tokens
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "gclsrc", "dclid", "zanpid", "msclkid",
    "ref", "source", "_ga", "_gl", "mc_cid", "mc_eid"
}

# Elements and CSS classes typically representing noise/boilerplates
NOISE_TAGS = [
    "script", "style", "noscript", "iframe", "svg", "canvas",
    "nav", "footer", "header", "aside", "form"
]

NOISE_SELECTORS = [
    ".ad", ".ads", ".advertisement", ".banner",
    ".cookie", ".cookie-banner", ".gdpr", ".consent",
    ".popup", ".modal", ".newsletter-signup",
    ".social-share", ".share-buttons", ".sidebar",
    ".comments-area", ".disclaimer", "#disclaimer"
]


def clean_hyperlink(url: str, base_url: str) -> str:
    """Resolves relative links and removes tracking parameters."""
    if not url:
        return ""
    absolute_url = urljoin(base_url, url)
    try:
        parsed = urlparse(absolute_url)
        query_dict = parse_qs(parsed.query, keep_blank_values=False)
        cleaned_query = {k: v for k, v in query_dict.items() if k.lower() not in TRACKING_PARAMS}
        new_query = urlencode(cleaned_query, doseq=True)
        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment if not new_query else "",
        ))
    except Exception:
        return absolute_url


def estimate_tokens(text: str) -> int:
    """Estimates token count for LLM context window using standard character/word ratio."""
    if not text:
        return 0
    # Standard rule of thumb: ~4 characters per token in English, ~3 in multilingual
    return max(1, int(len(text) / 3.8))


class AIMarkdownCleaner:
    """
    Parses HTML DOM, strips noise, extracts metadata, and generates clean,
    hierarchical Markdown tailored specifically for LLMs.
    """

    def __init__(
        self,
        base_url: str,
        include_links: bool = True,
        include_images: bool = True,
    ):
        self.base_url = base_url
        self.include_links = include_links
        self.include_images = include_images
        self.extracted_links: List[Dict[str, str]] = []
        self._seen_urls = set()

    def extract_metadata(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extracts rich document metadata (title, description, author, etc.)."""
        parsed_url = urlparse(self.base_url)
        domain = parsed_url.netloc

        # Title resolution
        title = None
        for selector in [
            lambda: soup.find("meta", property="og:title"),
            lambda: soup.find("meta", attrs={"name": "twitter:title"}),
            lambda: soup.find("title"),
            lambda: soup.find("h1"),
        ]:
            node = selector()
            if node:
                title = node.get("content") or node.get_text()
                if title:
                    title = title.strip()
                    break

        # Description
        description = None
        for selector in [
            lambda: soup.find("meta", attrs={"name": "description"}),
            lambda: soup.find("meta", property="og:description"),
            lambda: soup.find("meta", attrs={"name": "twitter:description"}),
        ]:
            node = selector()
            if node:
                description = node.get("content")
                if description:
                    description = description.strip()
                    break

        # Author
        author = None
        for selector in [
            lambda: soup.find("meta", attrs={"name": "author"}),
            lambda: soup.find("meta", property="article:author"),
            lambda: soup.find("a", attrs={"rel": "author"}),
        ]:
            node = selector()
            if node:
                author = node.get("content") or node.get_text()
                if author:
                    author = author.strip()
                    break

        # Published time
        published_time = None
        for selector in [
            lambda: soup.find("meta", property="article:published_time"),
            lambda: soup.find("time", attrs={"datetime": True}),
        ]:
            node = selector()
            if node:
                published_time = node.get("content") or node.get("datetime")
                if published_time:
                    published_time = published_time.strip()
                    break

        # Language
        html_tag = soup.find("html")
        lang = html_tag.get("lang") if html_tag else None

        # Canonical URL
        canonical_tag = soup.find("link", rel="canonical")
        canonical_url = canonical_tag.get("href") if canonical_tag else self.base_url

        # Favicon
        icon_tag = soup.find("link", rel=re.compile(r"^(shortcut )?icon$", re.I))
        favicon = urljoin(self.base_url, icon_tag.get("href")) if icon_tag and icon_tag.get("href") else None

        return {
            "title": title or domain,
            "description": description,
            "author": author,
            "published_time": published_time,
            "language": lang,
            "canonical_url": canonical_url,
            "domain": domain,
            "favicon": favicon,
        }

    def clean_dom(self, soup: BeautifulSoup) -> Tag:
        """Removes scripts, styles, comments, and boilerplate noise."""
        # 1. Remove comments
        for comment in soup.find_all(text=lambda text: isinstance(text, Comment)):
            comment.extract()

        # 2. Remove noise tags
        for tag_name in NOISE_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # 3. Remove noise classes/ids
        for selector in NOISE_SELECTORS:
            try:
                for match in soup.select(selector):
                    match.decompose()
            except Exception:
                continue

        # 4. Target main body container if present
        main_container = (
            soup.find("article")
            or soup.find("main")
            or soup.find(id=re.compile(r"(content|main|article|post|body)", re.I))
            or soup.find(class_=re.compile(r"(content|main|article|post|body)", re.I))
            or soup.find("body")
            or soup
        )
        return main_container

    def to_markdown(self, element: Tag) -> str:
        """Recursively converts clean HTML DOM into clean Markdown format."""
        lines: List[str] = []

        def walk(node):
            if isinstance(node, NavigableString):
                text = str(node)
                if text.strip():
                    lines.append(text)
                return

            if not isinstance(node, Tag):
                return

            name = node.name.lower()

            # Headings
            if name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
                level = int(name[1])
                heading_text = node.get_text().strip()
                if heading_text:
                    lines.append(f"\n\n{'#' * level} {heading_text}\n\n")
                return

            # Paragraphs & text blocks
            if name == "p":
                lines.append("\n\n")
                for child in node.children:
                    walk(child)
                lines.append("\n\n")
                return

            # Line break
            if name == "br":
                lines.append("\n")
                return

            # Blockquotes
            if name == "blockquote":
                quote_text = node.get_text().strip()
                if quote_text:
                    formatted_quote = "\n".join(f"> {line}" for line in quote_text.splitlines())
                    lines.append(f"\n\n{formatted_quote}\n\n")
                return

            # Preformatted / Code blocks
            if name in ["pre", "code"]:
                code_text = node.get_text()
                lang = ""
                # Attempt to extract language from class like class="language-python"
                classes = node.get("class", [])
                for c in classes:
                    if "language-" in c:
                        lang = c.replace("language-", "")
                        break
                if name == "pre" or "\n" in code_text:
                    lines.append(f"\n\n```{lang}\n{code_text.strip()}\n```\n\n")
                else:
                    lines.append(f" `{code_text.strip()}` ")
                return

            # Unordered & Ordered lists
            if name in ["ul", "ol"]:
                lines.append("\n\n")
                for idx, li in enumerate(node.find_all("li", recursive=False)):
                    prefix = f"{idx + 1}." if name == "ol" else "-"
                    li_text = li.get_text().strip()
                    if li_text:
                        lines.append(f"{prefix} {li_text}\n")
                lines.append("\n")
                return

            # Tables to Markdown
            if name == "table":
                md_table = self._convert_table(node)
                if md_table:
                    lines.append(f"\n\n{md_table}\n\n")
                return

            # Hyperlinks
            if name == "a":
                text = node.get_text().strip()
                href = node.get("href")
                if href and text and self.include_links:
                    clean_href = clean_hyperlink(href, self.base_url)
                    if clean_href.startswith("http") and clean_href not in self._seen_urls:
                        self._seen_urls.add(clean_href)
                        self.extracted_links.append({"text": text, "url": clean_href})
                    lines.append(f" [{text}]({clean_href}) ")
                elif text:
                    lines.append(f" {text} ")
                return

            # Images
            if name == "img" and self.include_images:
                alt = node.get("alt", "").strip() or "Image"
                src = node.get("src")
                if src:
                    clean_src = urljoin(self.base_url, src)
                    lines.append(f" ![{alt}]({clean_src}) ")
                return

            # Formatting
            if name in ["strong", "b"]:
                lines.append(" **")
                for child in node.children:
                    walk(child)
                lines.append("** ")
                return

            if name in ["em", "i"]:
                lines.append(" *")
                for child in node.children:
                    walk(child)
                lines.append("* ")
                return

            # Recurse through generic containers
            for child in node.children:
                walk(child)

        walk(element)

        raw_md = "".join(lines)
        # Normalize whitespace and collapse 3+ consecutive newlines into 2
        clean_md = re.sub(r"[ \t]+", " ", raw_md)
        clean_md = re.sub(r"\n\s*\n\s*\n+", "\n\n", clean_md)
        return clean_md.strip()

    def _convert_table(self, table_tag: Tag) -> str:
        """Converts HTML table into a clean Markdown table."""
        rows = table_tag.find_all("tr")
        if not rows:
            return ""

        table_data = []
        for row in rows:
            cols = [col.get_text().strip().replace("\n", " ") for col in row.find_all(["th", "td"])]
            if cols:
                table_data.append(cols)

        if not table_data:
            return ""

        # Normalize number of columns
        max_cols = max(len(r) for r in table_data)
        normalized_data = [r + [""] * (max_cols - len(r)) for r in table_data]

        lines = []
        header = normalized_data[0]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join(["---"] * max_cols) + " |")

        for row in normalized_data[1:]:
            lines.append("| " + " | ".join(row) + " |")

        return "\n".join(lines)


class AIExtractor:
    """
    Main High-Level Extractor.
    Orchestrates fast HTTP requests, transparent fallback to Playwright Chromium,
    DOM cleaning, and metadata extraction.
    """

    def extract(
        self,
        url: str,
        mode: str = "auto",
        format_type: str = "markdown",
        include_links: bool = True,
        include_images: bool = True,
        wait_for_selector: Optional[str] = None,
        timeout_seconds: int = 30,
    ) -> Dict[str, Any]:
        """
        Extracts and converts website content into AI-optimized Markdown.
        """
        url = normalize_url(url)
        validate_url_for_ssrf(url)
        logger.info("Extracting AI content from URL: %s (mode=%s)", url, mode)
        html = ""
        mode_used = "fast"

        # 1. Fetch HTML content according to selected mode
        if mode == "fast":
            html = self._fetch_fast_http(url, timeout_seconds)
            mode_used = "fast"

        elif mode == "browser":
            html = self._fetch_playwright(url, wait_for_selector, timeout_seconds)
            mode_used = "browser"

        else:  # mode == "auto"
            try:
                html = self._fetch_fast_http(url, timeout=min(timeout_seconds, 10))
                # Check if page is protected by Cloudflare or is an empty SPA skeleton
                if self._needs_browser_rendering(html):
                    logger.info("Page appears JS-heavy or protected. Switching to Playwright Chromium...")
                    html = self._fetch_playwright(url, wait_for_selector, timeout_seconds)
                    mode_used = "browser"
                else:
                    mode_used = "fast"
            except Exception as e:
                logger.warning("Fast HTTP fetch failed (%s). Falling back to Playwright Chromium...", e)
                html = self._fetch_playwright(url, wait_for_selector, timeout_seconds)
                mode_used = "browser"

        # 2. Parse and clean HTML
        soup = BeautifulSoup(html, "html.parser")
        cleaner = AIMarkdownCleaner(
            base_url=url,
            include_links=include_links,
            include_images=include_images,
        )

        metadata = cleaner.extract_metadata(soup)
        clean_container = cleaner.clean_dom(soup)
        markdown_body = cleaner.to_markdown(clean_container)

        # 3. Optional trafilatura enhancement if available and beneficial
        try:
            import trafilatura
            trafil_text = trafilatura.extract(
                html,
                url=url,
                output_format="markdown",
                include_links=include_links,
                include_images=include_images,
                include_tables=True,
            )
            if trafil_text and len(trafil_text) > len(markdown_body) * 0.7:
                markdown_body = trafil_text.strip()
        except Exception:
            pass

        # 4. Format content according to requested format
        final_content = markdown_body
        if format_type == "text":
            final_content = re.sub(r"[#*_`\[\]\(\)]", "", markdown_body)
        elif format_type == "summary":
            # Add YAML frontmatter header
            frontmatter = (
                f"---\n"
                f"title: \"{metadata.get('title')}\"\n"
                f"url: \"{url}\"\n"
                f"author: \"{metadata.get('author') or 'N/A'}\"\n"
                f"published: \"{metadata.get('published_time') or 'N/A'}\"\n"
                f"---\n\n"
            )
            final_content = frontmatter + markdown_body

        words = len(final_content.split())
        tokens = estimate_tokens(final_content)
        reading_time = round(words / 200, 1) if words else 0.0

        return {
            "status": "success",
            "url": url,
            "mode_used": mode_used,
            "tokens_estimated": tokens,
            "word_count": words,
            "reading_time_minutes": reading_time,
            "metadata": metadata,
            "content": final_content,
            "links": cleaner.extracted_links,
        }

    def _fetch_fast_http(self, url: str, timeout: int) -> str:
        """High-performance direct HTTP request mimicking desktop browser."""
        headers = {
            "User-Agent": get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Linux"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        }
        def _on_redirect(response: httpx.Response):
            if response.is_redirect:
                location = response.headers.get("Location")
                if location:
                    redirect_target = str(response.url.join(location))
                    validate_url_for_ssrf(redirect_target)

        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers=headers,
            event_hooks={"response": [_on_redirect]},
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text

    def _fetch_playwright(
        self,
        url: str,
        wait_for_selector: Optional[str],
        timeout_seconds: int,
    ) -> str:
        """Fetches and hydrates webpage using persistent Playwright Chromium."""
        with BasePlaywrightScraper(timeout_ms=timeout_seconds * 1000) as scraper:
            page = scraper.new_page()
            page.goto(url, wait_until="domcontentloaded")
            human_sleep(0.5, 1.5)

            if wait_for_selector:
                try:
                    page.wait_for_selector(wait_for_selector, timeout=8000)
                except Exception:
                    logger.warning("Selector '%s' did not appear within timeout.", wait_for_selector)

            # Human scroll to trigger dynamic lazy loading
            human_scroll(page, steps=2, min_distance=200, max_distance=400)
            return page.content()

    def _needs_browser_rendering(self, html: str) -> bool:
        """Detects if page is an empty SPA shell or Cloudflare challenge."""
        html_lower = html.lower()

        # Cloudflare / anti-bot challenges
        if any(token in html_lower for token in [
            "cf-browser-verification",
            "checking your browser",
            "just a moment...",
            "turnstile",
            "challenge-running",
            "enable javascript to run this app",
            "you need to enable javascript to run this app",
        ]):
            return True

        # Parse structure
        soup = BeautifulSoup(html, "html.parser")
        body = soup.find("body")
        if not body:
            return True

        text_content = body.get_text().strip()
        # If the page already has reasonable text or standard content elements, it doesn't need a browser
        if len(text_content) >= 100:
            return False

        # If text is very short (< 100 chars), check if it's an empty SPA mount container
        has_spa_mount = bool(body.find(id=re.compile(r"^(root|app|__next)$", re.I)))
        if has_spa_mount or len(text_content) < 30:
            return True

        return False


extractor = AIExtractor()
ai_extractor = extractor
