"""
Pydantic schemas for API request and response validation.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class QuoteETLRequest(BaseModel):
    source_url: str = Field(
        default="https://quotes.toscrape.com",
        description="Target website URL for quote scraping",
    )
    tag: Optional[str] = Field(
        default=None,
        description="Optional tag filter (e.g., 'love', 'inspirational', 'life')",
    )
    max_items: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of items to scrape",
    )
    webhook_url: Optional[str] = Field(
        default=None,
        description="Outbound webhook URL to notify upon pipeline completion",
    )


class ParallelETLRequest(BaseModel):
    pages: List[int] = Field(
        default=[1, 2],
        description="List of page numbers to scrape concurrently across workers",
    )
    webhook_url: Optional[str] = Field(
        default=None,
        description="Outbound webhook URL to notify upon aggregation",
    )


class IncomingWebhookPayload(BaseModel):
    event: str = Field(..., description="Event name/type (e.g. 'order.created', 'scrape.trigger')")
    source: str = Field(default="external_system", description="Origin service identifier")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary event payload")


class FlowTriggerResponse(BaseModel):
    task_id: str
    flow_name: str
    status: str
    message: str
    status_url: str
    vnc_urls: Dict[str, str]


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    ready: bool
    successful: Optional[bool] = None
    result: Optional[Any] = None
    error: Optional[str] = None
