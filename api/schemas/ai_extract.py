"""
Pydantic schemas for AI-optimized web content extraction.
"""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator
from core.utils import normalize_url


class AIExtractRequest(BaseModel):
    url: str = Field(
        ...,
        description="Target URL (e.g. 'github.com/torvalds/linux', 'g1.globo.com', 'https://example.com')",
        examples=["github.com/torvalds/linux", "pt.wikipedia.org/wiki/Python"],
    )

    @field_validator("url", mode="before")
    @classmethod
    def validate_and_normalize_url(cls, v: str) -> str:
        return normalize_url(v)
    mode: Literal["auto", "browser", "fast"] = Field(
        default="auto",
        description=(
            "'auto': Tries fast HTTP first; falls back to Playwright if blocked or JS-heavy.\n"
            "'browser': Always renders via full Playwright Chromium (stealth + dynamic JS).\n"
            "'fast': Only fast HTTP request (low latency, best for static/SSR sites)."
        ),
    )
    format: Literal["markdown", "text", "json", "summary"] = Field(
        default="markdown",
        description="Output format optimized for LLMs: 'markdown' (standard), 'text', 'json', or 'summary'.",
    )
    include_links: bool = Field(
        default=True,
        description="Preserve useful markdown links without tracking query parameters",
    )
    include_images: bool = Field(
        default=True,
        description="Preserve image markdown with alt text",
    )
    wait_for_selector: Optional[str] = Field(
        default=None,
        description="Optional CSS selector to wait for before extracting in browser mode",
    )
    timeout_seconds: int = Field(
        default=30,
        ge=5,
        le=120,
        description="Timeout in seconds for fetching and rendering",
    )


class AIExtractMetadata(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    author: Optional[str] = None
    published_time: Optional[str] = None
    language: Optional[str] = None
    canonical_url: Optional[str] = None
    domain: str
    favicon: Optional[str] = None


class AIExtractResponse(BaseModel):
    status: str = Field(default="success")
    url: str
    mode_used: str = Field(description="'fast' (HTTP) or 'browser' (Playwright Chromium)")
    tokens_estimated: int = Field(description="Estimated token count for LLM context window")
    word_count: int
    reading_time_minutes: float
    metadata: AIExtractMetadata
    content: str = Field(description="Clean, sanitized Markdown or text ready for LLM prompt injection")
    links: List[Dict[str, str]] = Field(default_factory=list, description="Extracted hyperlinks with cleaned anchor text")


class AIBatchExtractRequest(BaseModel):
    urls: List[str] = Field(..., min_length=1, max_length=50, description="List of URLs to scrape in parallel")
    mode: Literal["auto", "browser", "fast"] = Field(default="auto")
    webhook_url: Optional[str] = Field(default=None, description="Optional outbound webhook URL for batch completion notification")

    @field_validator("urls", mode="before")
    @classmethod
    def validate_and_normalize_urls(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [normalize_url(u) for u in v]
        return v
