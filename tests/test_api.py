"""
Unit tests for FastAPI endpoints using TestClient.
"""

from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Omni-Flow Scraping & Automation Platform"
    assert "vnc_live_streams" in data


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data


def test_vnc_info_endpoint():
    response = client.get("/api/v1/vnc-info")
    assert response.status_code == 200
    data = response.json()
    assert "worker_1_novnc" in data
    assert "worker_2_novnc" in data


def test_incoming_webhook_endpoint():
    payload = {
        "event": "customer.inquiry",
        "source": "zendesk",
        "payload": {"ticket_id": 999}
    }
    response = client.post("/api/v1/webhooks/incoming", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ACCEPTED"
    assert data["event_received"] == "customer.inquiry"
