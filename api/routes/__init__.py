"""Routes package."""
from api.routes.flows import router as flows_router
from api.routes.tasks import router as tasks_router
from api.routes.webhooks import router as webhooks_router
from api.routes.ai_extract import router as ai_extract_router
from api.routes.google_search import router as google_search_router
from api.routes.leads import router as leads_router
from api.routes.proposals import router as proposals_router
from api.routes.sync import router as sync_router
from api.routes.editorial import router as editorial_router
from api.routes.enrichment import router as enrichment_router

__all__ = [
    "flows_router",
    "tasks_router",
    "webhooks_router",
    "ai_extract_router",
    "google_search_router",
    "leads_router",
    "proposals_router",
    "sync_router",
    "editorial_router",
    "enrichment_router",
]
