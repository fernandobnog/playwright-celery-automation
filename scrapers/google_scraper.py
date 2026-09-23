"""
Google Search Scraper with Playwright.
Performs resilient Google Search extractions simulating real human navigation:
- Organic search results (Title, True URL, Display Breadcrumb, Snippet, Date, Sitelinks)
- People Also Ask (Perguntas Frequentes / Relacionadas)
- Related Searches (Pesquisas Relacionadas no rodapé)
- AI Overview / Knowledge Panel (if present)
- Consent banner auto-dismiss
- Anti-detection stealth & persistent browser profile (cookies & history)
"""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urljoin, parse_qs, urlparse, unquote

import bs4
import httpx
from playwright.sync_api import Page

from scrapers.base import BasePlaywrightScraper
from scrapers.humanizer import human_scroll, human_sleep

logger = logging.getLogger(__name__)


class GoogleSearchScraper(BasePlaywrightScraper):
    """
    Automated Google Search scraper utilizing persistent Chromium profiles
    to emulate organic user searches and gather rich SERP data.
    """

    def search(
        self,
        query: str,
        num_results: int = 10,
        lang: str = "pt-BR",
        country: str = "br",
        capture_screenshot: bool = False,
    ) -> Dict[str, Any]:
        """
        Navigates to Google Search, scrolls to load full results,
        and parses organic results, PAA, and related queries.
        """
        page = self.new_page()
        search_url = (
            f"https://www.google.com/search?q={quote_plus(query)}"
            f"&hl={lang}&gl={country}&num={max(num_results, 10)}"
        )

        logger.info("Performing Google Search: '%s' (num=%d, lang=%s)", query, num_results, lang)
        page.goto(search_url, wait_until="domcontentloaded")
        human_sleep(1.0, 2.0)

        # 1. Dismiss Google Consent / Cookie dialog if it pops up
        self._dismiss_consent_dialog(page)

        # 2. Smooth scroll to trigger dynamic lazy loading and populate at least num_results
        human_scroll(page, steps=3, min_distance=250, max_distance=500)
        human_sleep(0.5, 1.2)

        # 3. Capture optional audit screenshot
        screenshot_path = None
        if capture_screenshot:
            screenshot_path = self.save_screenshot(page, prefix=f"google_{quote_plus(query[:20])}")

        # 4. Parse rendered DOM
        html_content = page.content()
        soup = bs4.BeautifulSoup(html_content, "html.parser")

        # 5. Extract all rich SERP elements
        parsed_data = self._parse_serp(soup, query=query, num_results=num_results)

        # 6. Multi-Search Engine Fallback (DuckDuckGo) if Google triggers CAPTCHA or yields 0 results
        if "google.com/sorry" in page.url or len(parsed_data.get("organic_results", [])) == 0:
            logger.warning("Google search blocked by CAPTCHA/sorry or returned 0 results for '%s'. Falling back to DuckDuckGo...", query[:40])
            ddg_data = self._search_duckduckgo_fallback(page, query=query, num_results=num_results)
            if ddg_data and len(ddg_data.get("organic_results", [])) > 0:
                parsed_data = ddg_data

        if screenshot_path:
            parsed_data["screenshot_path"] = screenshot_path

        return parsed_data

    def _search_duckduckgo_fallback(self, page: Page, query: str, num_results: int) -> Dict[str, Any]:
        """Fallback to DuckDuckGo SERP when Google is blocked by bot detection."""
        try:
            # DuckDuckGo handles queries better without complex boolean operators or excessive quotes
            clean_query = re.sub(r'["()]+', ' ', query)
            clean_query = re.sub(r'\bOR\b', ' ', clean_query)
            clean_query = re.sub(r'\s+', ' ', clean_query).strip()

            ddg_url = f"https://duckduckgo.com/?q={quote_plus(clean_query)}&ia=web"
            logger.info("Executing DuckDuckGo fallback search: '%s' (cleaned: '%s')", query[:40], clean_query[:40])
            page.goto(ddg_url, wait_until="domcontentloaded")
            try:
                page.wait_for_selector("a[data-testid='result-title-a'], article, .result", timeout=4000)
            except Exception:
                page.wait_for_timeout(2000)
            human_scroll(page, steps=2, min_distance=200, max_distance=400)

            soup = bs4.BeautifulSoup(page.content(), "html.parser")
            organic_results: List[Dict[str, Any]] = []
            seen_urls = set()

            title_anchors = soup.select("a[data-testid='result-title-a'], h2 a, .result__title a")
            for a_tag in title_anchors:
                raw_href = a_tag.get("href", "")
                if not raw_href or "duckduckgo.com/y.js" in raw_href:
                    continue

                if "uddg=" in raw_href:
                    qs = parse_qs(urlparse(raw_href).query)
                    target_url = unquote(qs.get("uddg", [raw_href])[0])
                else:
                    target_url = raw_href

                if not target_url or target_url in seen_urls or "duckduckgo.com" in target_url:
                    continue
                seen_urls.add(target_url)

                title = a_tag.get_text().strip()
                if not title:
                    continue

                parent = a_tag.find_parent("article") or a_tag.find_parent("li") or a_tag.find_parent("div")
                snippet = ""
                if parent:
                    snip_el = parent.select_one("div[data-result='snippet'], .result__snippet, [data-testid='result-snippet']")
                    if snip_el:
                        snippet = snip_el.get_text().strip()

                organic_results.append({
                    "position": len(organic_results) + 1,
                    "title": title,
                    "url": target_url,
                    "display_url": target_url,
                    "snippet": snippet,
                    "date": None,
                    "sitelinks": [],
                })
                if len(organic_results) >= num_results:
                    break

            logger.info("DuckDuckGo fallback retrieved %d results for '%s'", len(organic_results), clean_query[:40])
            return {
                "query": query,
                "total_results": len(organic_results),
                "organic_results": organic_results,
                "people_also_ask": [],
                "related_searches": [],
                "ai_overview": None,
            }
        except Exception as e_ddg:
            logger.warning("DuckDuckGo fallback search failed for '%s': %s", query[:40], e_ddg)
            return {"query": query, "total_results": 0, "organic_results": []}

    def _dismiss_consent_dialog(self, page: Page):
        """Clicks 'Aceitar tudo' / 'Concordo' / 'Accept all' if presented."""
        consent_selectors = [
            "button:has-text('Aceitar tudo')",
            "button:has-text('Concordo')",
            "button:has-text('Accept all')",
            "button:has-text('I agree')",
            "button:has-text('Tudo aceitar')",
        ]
        for sel in consent_selectors:
            try:
                btn = page.locator(sel)
                if btn.count() > 0 and btn.first.is_visible():
                    logger.info("Dismissing Google consent modal...")
                    btn.first.click()
                    human_sleep(0.8, 1.5)
                    break
            except Exception:
                pass

    def _parse_serp(self, soup: bs4.BeautifulSoup, query: str, num_results: int) -> Dict[str, Any]:
        """Extracts structured search results, People Also Ask, and Related Searches."""
        organic_results: List[Dict[str, Any]] = []
        seen_urls = set()

        # Reusable HTTP client with connection pool for fast /goto?url= redirect resolution
        with httpx.Client(follow_redirects=False, timeout=5) as http_client:
            raw_blocks = soup.select("div.N54PNb, div.MjjYud, div.g")

            for block in raw_blocks:
                h3 = block.select_one("h3")
                if not h3:
                    continue

                title = h3.get_text().strip()
                # Ignore AI overview headers or generic sections
                if not title or title.lower().startswith("o modo ia") or title.lower().startswith("perguntas"):
                    continue

                a_tag = h3.find_parent("a")
                if not a_tag:
                    continue

                raw_href = a_tag.get("href", "")
                if not raw_href or raw_href.startswith("#") or "google.com/search" in raw_href or "accounts.google.com" in raw_href:
                    continue

                # Resolve true destination URL
                target_url = self._resolve_target_url(raw_href, http_client)
                if not target_url or target_url in seen_urls:
                    continue
                seen_urls.add(target_url)

                # Breadcrumb / Domain
                cite_el = block.select_one("cite")
                display_url = cite_el.get_text().strip() if cite_el else ""

                # Snippet and Date extraction
                snippet = ""
                date_published = None

                # Search through child blocks for snippet text
                for child in block.find_all("div", recursive=False):
                    txt = child.get_text().strip()
                    if title not in txt and len(txt) > 25:
                        snippet = txt
                        break

                if not snippet:
                    for sub in block.select("div.VwiC3b, div[style*='-webkit-line-clamp'], span.aCOpRe"):
                        stxt = sub.get_text().strip()
                        if stxt and len(stxt) > 20:
                            snippet = stxt
                            break

                # Separate leading date (e.g. 'há 4 horas —' or '9 de ago. de 2024 —')
                if " — " in snippet:
                    parts = snippet.split(" — ", 1)
                    if len(parts[0]) < 35:
                        date_published = parts[0].strip()
                        snippet = parts[1].strip()

                # Sitelinks under this result (if any)
                sitelinks = []
                for sl in block.select("div.usJj9c a, table.HiHjCd a, div.l a"):
                    s_title = sl.get_text().strip()
                    s_href = sl.get("href", "")
                    if s_title and s_href and s_href != raw_href and not s_href.startswith("#"):
                        resolved_sl = self._resolve_target_url(s_href, http_client)
                        sitelinks.append({"title": s_title, "url": resolved_sl})

                organic_results.append({
                    "position": len(organic_results) + 1,
                    "title": title,
                    "url": target_url,
                    "display_url": display_url,
                    "snippet": snippet,
                    "date": date_published,
                    "sitelinks": sitelinks,
                })

                if len(organic_results) >= num_results:
                    break

        # People Also Ask (Perguntas Relacionadas)
        people_also_ask: List[str] = []
        for q_el in soup.select("div[data-q], div.related-question-pair"):
            q_txt = q_el.get_text().strip()
            if q_txt and "?" in q_txt:
                clean_q = q_txt.split("?")[0] + "?"
                if clean_q not in people_also_ask and len(clean_q) < 140:
                    people_also_ask.append(clean_q)

        # Related Searches (Pesquisas Relacionadas no rodapé)
        related_searches: List[str] = []
        ignored_keywords = [
            "modo ia", "ferramentas", "configurações", "ajuda", "privacidade",
            "termos", "próxima", "mais resultados", "imagens", "vídeos",
            "notícias", "shopping", "livros", "última", "filtro", "ver tudo",
        ]
        for rel_a in soup.select("a[href*='/search?']"):
            rel_txt = rel_a.get_text().strip()
            if rel_txt and 4 < len(rel_txt) < 80:
                low = rel_txt.lower()
                if not any(k in low for k in ignored_keywords):
                    if "sa=X" in rel_a.get("href", "") or "ved=" in rel_a.get("href", ""):
                        if rel_txt not in related_searches:
                            related_searches.append(rel_txt)

        # AI Overview / Summary (if available)
        ai_overview = None
        ai_box = soup.select_one("div[data-attrid='wa:/description'], div.xpdopen, div.g[data-md]")
        if ai_box:
            ai_text = ai_box.get_text().strip()
            if ai_text and len(ai_text) > 40:
                ai_overview = ai_text[:500]

        return {
            "query": query,
            "total_results": len(organic_results),
            "organic_results": organic_results,
            "people_also_ask": people_also_ask[:8],
            "related_searches": related_searches[:10],
            "ai_overview": ai_overview,
        }

    def _resolve_target_url(self, href: str, client: httpx.Client) -> str:
        """Resolves Google /goto?url= redirects and /url?q= wrappers to actual URLs."""
        if href.startswith("/goto?url="):
            full_goto = urljoin("https://www.google.com", href)
            try:
                resp = client.get(full_goto, timeout=4)
                if resp.status_code in (301, 302, 303, 307, 308):
                    return resp.headers.get("location") or full_goto
            except Exception as e:
                logger.debug("Failed resolving /goto link %s: %s", href, e)
            return full_goto

        if href.startswith("/url?"):
            match = re.search(r"/url\?q=([^&]+)", href)
            if match:
                return match.group(1)

        return href


google_scraper = GoogleSearchScraper()
