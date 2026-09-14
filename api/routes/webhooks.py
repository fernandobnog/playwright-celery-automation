"""
API Routes for inbound webhooks from external third-party services.
Provides seamless no-code replacement for n8n webhook nodes.
"""

import logging
from fastapi import APIRouter, BackgroundTasks
from api.schemas.requests import IncomingWebhookPayload
from flows.example_flow import trigger_quote_etl_flow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks Inbound"])


@router.post("/incoming")
def receive_external_webhook(payload: IncomingWebhookPayload):
    """
    Receives incoming webhook event from external systems (e.g. CRMs, e-commerces).
    Demonstrates routing external triggers into Python Celery automations automatically.
    """
    logger.info("Received incoming webhook event '%s' from '%s'", payload.event, payload.source)

    task_id = None
    # If the webhook event requests a scrape, trigger pipeline autonomously
    if payload.event in ["scrape.trigger", "order.created", "product.sync"]:
        tag = payload.payload.get("tag")
        limit = payload.payload.get("max_items", 5)
        callback = payload.payload.get("webhook_url")

        async_result = trigger_quote_etl_flow(
            tag=tag,
            max_items=limit,
            webhook_url=callback,
        )
        task_id = async_result.id

    return {
        "status": "ACCEPTED",
        "event_received": payload.event,
        "source": payload.source,
        "triggered_flow_task_id": task_id,
        "message": "Webhook processed and pipeline scheduled." if task_id else "Event logged.",
    }
