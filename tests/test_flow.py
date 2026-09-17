"""
Tests for ETL transformation logic and storage persistence.
"""

import os
import tempfile
import pytest
from flows.tasks_etl import task_transform_quotes
from scrapers.stealth import get_random_user_agent, get_random_viewport
from storage.repository import PipelineRepository


def test_random_stealth_generators():
    ua = get_random_user_agent()
    assert "Mozilla/5.0" in ua
    
    vp = get_random_viewport()
    assert "width" in vp and "height" in vp
    assert vp["width"] >= 1024


def test_task_transform_quotes():
    raw_payload = {
        "source_url": "https://quotes.toscrape.com",
        "screenshot_path": "/app/downloads/test.png",
        "items": [
            {
                "quote": "The world as we have created it is a process of our thinking.",
                "author": "Albert Einstein",
                "tags": ["change", "deep-thought", "thinking"],
            },
            {
                "quote": "It is our choices that show what we truly are.",
                "author": "J.K. Rowling",
                "tags": ["choices"],
            }
        ]
    }

    result = task_transform_quotes.apply(args=[raw_payload]).get()

    assert result["total_items"] == 2
    assert "analytics" in result
    assert result["analytics"]["unique_tags_count"] == 4
    assert result["items"][0]["word_count"] == 13
    assert result["items"][0]["is_short_quote"] is True


def test_storage_repository():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        repo = PipelineRepository(db_path=db_path)
        repo.log_flow_start("test-task-123", "test_flow", {"foo": "bar"})

        repo.save_scraped_items(
            task_id="test-task-123",
            source_url="https://example.com",
            items=[{"quote": "Test Quote", "author": "Tester", "tags": ["test"]}],
        )

        repo.log_flow_complete("test-task-123", {"status": "SUCCESS"})

        with repo._get_connection() as conn:
            item = conn.execute("SELECT * FROM scraped_items WHERE task_id = 'test-task-123'").fetchone()
            assert item is not None
            assert item["author"] == "Tester"

            flow = conn.execute("SELECT * FROM flow_executions WHERE task_id = 'test-task-123'").fetchone()
            assert flow is not None
            assert flow["status"] == "SUCCESS"
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
