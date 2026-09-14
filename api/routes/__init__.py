"""Routes package."""
from api.routes.flows import router as flows_router
from api.routes.tasks import router as tasks_router
from api.routes.webhooks import router as webhooks_router
from api.routes.ai_extract import router as ai_extract_router

__all__ = ["flows_router", "tasks_router", "webhooks_router", "ai_extract_router"]
