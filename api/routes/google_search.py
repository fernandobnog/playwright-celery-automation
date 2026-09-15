"""
FastAPI Routes for Google Search Scraping.
Provides both structured JSON API and zero-friction Markdown reader endpoints.
"""

import logging
from typing import Literal
from fastapi import APIRouter, HTTPException, Query, Response

from api.schemas.google_search import (
    GoogleSearchRequest,
    GoogleSearchResponse,
    GoogleSearchResultItem,
)
from flows.tasks_google_search import task_google_search

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Google Search Scraper"])


def _format_search_as_markdown(data: dict) -> str:
    """Formats Google SERP data into clean Markdown optimized for LLM prompting."""
    query = data.get("query", "")
    results = data.get("organic_results", [])
    paa = data.get("people_also_ask", [])
    related = data.get("related_searches", [])
    ai_overview = data.get("ai_overview")

    md = f"# Google Search Results: \"{query}\"\n\n"
    md += f"**Total Organic Results:** {len(results)}\n\n"

    if ai_overview:
        md += f"### AI Overview / Knowledge Summary\n> {ai_overview}\n\n---\n\n"

    md += "## Organic Search Results\n\n"
    for item in results:
        pos = item.get("position", 1)
        title = item.get("title", "")
        url = item.get("url", "")
        disp = item.get("display_url", "")
        snip = item.get("snippet", "")
        date = item.get("date")

        md += f"### {pos}. [{title}]({url})\n"
        if disp:
            md += f"- **Display Link:** `{disp}`\n"
        if date:
            md += f"- **Date:** {date}\n"
        if snip:
            md += f"- **Snippet:** {snip}\n"

        sitelinks = item.get("sitelinks", [])
        if sitelinks:
            md += "- **Sitelinks:** " + ", ".join([f"[{sl['title']}]({sl['url']})" for sl in sitelinks]) + "\n"
        md += "\n"

    if paa:
        md += "## People Also Ask (Perguntas Relacionadas)\n"
        for q in paa:
            md += f"- {q}\n"
        md += "\n"

    if related:
        md += "## Related Searches (Pesquisas Relacionadas)\n"
        for r in related:
            md += f"- {r}\n"
        md += "\n"

    return md


@router.post("/api/v1/search/google", response_model=GoogleSearchResponse)
def search_google_post(payload: GoogleSearchRequest):
    """
    Executes a real-browser Google Search with persistent profile, anti-bot bypass,
    and extraction of organic rankings, snippets, PAA, and related terms.
    """
    try:
        # Execute via Celery task on scraping worker with Playwright
        async_task = task_google_search.apply_async(
            kwargs={
                "query": payload.query,
                "num_results": payload.num_results,
                "lang": payload.lang,
                "country": payload.country,
                "capture_screenshot": payload.capture_screenshot,
            }
        )
        data = async_task.get(timeout=60)

        if payload.format == "markdown":
            md_text = _format_search_as_markdown(data)
            return Response(content=md_text, media_type="text/markdown; charset=utf-8")

        return GoogleSearchResponse(
            query=data["query"],
            total_results=data["total_results"],
            organic_results=[GoogleSearchResultItem(**item) for item in data["organic_results"]],
            people_also_ask=data.get("people_also_ask", []),
            related_searches=data.get("related_searches", []),
            ai_overview=data.get("ai_overview"),
            screenshot_path=data.get("screenshot_path"),
        )
    except Exception as e:
        logger.error("Error executing Google search for '%s': %s", payload.query, e)
        raise HTTPException(status_code=500, detail=f"Failed to execute Google search: {e}")


@router.get("/api/v1/search/google")
def search_google_get(
    q: str = Query(..., min_length=1, description="Search query"),
    num: int = Query(10, ge=1, le=50, description="Target number of organic results (default 10)"),
    lang: str = Query("pt-BR", description="Language (pt-BR, en-US)"),
    country: str = Query("br", description="Country (br, us)"),
    format: Literal["json", "markdown"] = Query("json", description="Response format"),
    screenshot: bool = Query(False, description="Save screenshot"),
):
    """
    GET shortcut to perform Google search via query parameters.
    """
    req = GoogleSearchRequest(
        query=q,
        num_results=num,
        lang=lang,
        country=country,
        format=format,
        capture_screenshot=screenshot,
    )
    return search_google_post(req)


@router.get("/s/{query:path}")
def google_search_shortcut_reader(query: str):
    """
    Zero-friction Google Search reader endpoint (similar to /r/{url}).
    Usage:
      curl http://omniflow_api:8000/s/inteligencia+artificial+para+empresas -H "X-API-Key: YOUR_KEY"
    Returns pure formatted Markdown directly in response body.
    """
    req = GoogleSearchRequest(query=query, num_results=10, format="markdown")
    return search_google_post(req)
