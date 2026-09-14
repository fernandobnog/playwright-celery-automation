"""Flows package for end-to-end automation pipelines."""
from flows.example_flow import (
    build_quotes_pipeline,
    trigger_quote_etl_flow,
    trigger_scheduled_quote_etl,
)
from flows.parallel_flow import trigger_parallel_crawl_flow

__all__ = [
    "build_quotes_pipeline",
    "trigger_quote_etl_flow",
    "trigger_scheduled_quote_etl",
    "trigger_parallel_crawl_flow",
]
