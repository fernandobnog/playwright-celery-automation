"""
FastAPI Routes for AI-Optimized Web Content Extraction.
Features:
- POST /api/v1/extract: Structured JSON extraction with metadata & tokens
- GET /api/v1/extract: Query-parameter based extraction
- GET /r/{target_url:path}: Direct raw Markdown endpoint (Jina Reader / Firecrawl style)
- POST /api/v1/extract/batch: Asynchronous batch extraction via Celery
"""

import logging
from typing import Literal, Optional
from fastapi import APIRouter, HTTPException, Query, Response
from api.schemas.ai_extract import (
    AIBatchExtractRequest,
    AIExtractMetadata,
    AIExtractRequest,
    AIExtractResponse,
)
from flows.tasks_ai_extract import task_ai_extract_url, trigger_batch_ai_extraction
from scrapers.ai_extractor import extractor

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AI Web Extractor (LLM Optimized)"])


def execute_extraction(
    url: str,
    mode: str = "auto",
    format_type: str = "markdown",
    include_links: bool = True,
    include_images: bool = True,
    wait_for_selector: Optional[str] = None,
    timeout_seconds: int = 30,
) -> dict:
    if mode == "browser":
        task = task_ai_extract_url.apply_async(
            kwargs={
                "url": url,
                "mode": "browser",
                "format_type": format_type,
                "include_links": include_links,
                "include_images": include_images,
                "wait_for_selector": wait_for_selector,
            }
        )
        return task.get(timeout=timeout_seconds)

    try:
        return extractor.extract(
            url=url,
            mode=mode,
            format_type=format_type,
            include_links=include_links,
            include_images=include_images,
            wait_for_selector=wait_for_selector,
            timeout_seconds=timeout_seconds,
        )
    except Exception as e:
        if mode == "auto":
            logger.info("Fast extraction in auto mode failed (%s). Delegating to Playwright Celery worker...", e)
            task = task_ai_extract_url.apply_async(
                kwargs={
                    "url": url,
                    "mode": "browser",
                    "format_type": format_type,
                    "include_links": include_links,
                    "include_images": include_images,
                    "wait_for_selector": wait_for_selector,
                }
            )
            return task.get(timeout=timeout_seconds)
        raise e


@router.post("/api/v1/extract", response_model=AIExtractResponse)
def extract_ai_content_post(payload: AIExtractRequest):
    """
    Extracts any website and converts its content into AI-optimized Markdown.
    Removes ads, cookie notices, and navigation noise, preserving headings,
    tables, and semantic structure.
    """
    try:
        data = execute_extraction(
            url=payload.url,
            mode=payload.mode,
            format_type=payload.format,
            include_links=payload.include_links,
            include_images=payload.include_images,
            wait_for_selector=payload.wait_for_selector,
            timeout_seconds=payload.timeout_seconds,
        )
        return AIExtractResponse(
            status=data["status"],
            url=data["url"],
            mode_used=data["mode_used"],
            tokens_estimated=data["tokens_estimated"],
            word_count=data["word_count"],
            reading_time_minutes=data["reading_time_minutes"],
            metadata=AIExtractMetadata(**data["metadata"]),
            content=data["content"],
            links=data["links"],
        )
    except Exception as e:
        logger.error("Extraction error for URL %s: %s", payload.url, e)
        raise HTTPException(status_code=500, detail=f"Failed to extract content: {e}")


@router.get("/api/v1/extract", response_model=AIExtractResponse)
def extract_ai_content_get(
    url: str = Query(..., description="Target URL to extract"),
    mode: Literal["auto", "browser", "fast"] = Query("auto", description="Extraction mode"),
    format: Literal["markdown", "text", "summary"] = Query("markdown", description="Content format"),
    include_links: bool = Query(True, description="Include clean hyperlinks"),
):
    """
    GET shortcut to extract AI-optimized content via URL parameter.
    """
    req = AIExtractRequest(
        url=url,
        mode=mode,
        format=format,
        include_links=include_links,
    )
    return extract_ai_content_post(req)


@router.get("/r/{target_url:path}")
def jina_reader_style_endpoint(target_url: str):
    """
    Direct Raw Markdown Reader (Drop-in replacement for r.jina.ai).
    Usage:
      curl http://localhost:8000/r/https://en.wikipedia.org/wiki/Artificial_intelligence
    Returns pure text/markdown directly in response body for zero-friction LLM prompting.
    """
    # Ensure scheme
    url = target_url
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    try:
        data = execute_extraction(url=url, mode="auto", format_type="markdown")
        meta = data["metadata"]
        title = meta.get("title", "")
        desc = meta.get("description", "")

        header = f"# {title}\n\n"
        if desc:
            header += f"> {desc}\n\n"
        header += f"URL Source: {url}\nEstimated Tokens: {data['tokens_estimated']}\n\n---\n\n"

        full_markdown = header + data["content"]
        return Response(content=full_markdown, media_type="text/markdown; charset=utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read URL: {e}")


@router.post("/api/v1/extract/batch")
def extract_batch(payload: AIBatchExtractRequest):
    """
    Enqueues batch URL extraction in parallel across Celery scraping workers.
    Returns task ID to monitor progress.
    """
    try:
        chord_res = trigger_batch_ai_extraction(
            urls=payload.urls,
            mode=payload.mode,
            webhook_url=payload.webhook_url,
        )
        return {
            "status": "QUEUED",
            "task_id": chord_res.id,
            "total_urls": len(payload.urls),
            "status_url": f"/api/v1/tasks/{chord_res.id}",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to enqueue batch extraction: {e}")
