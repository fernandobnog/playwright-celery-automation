"""
FastAPI Application Gateway.
Provides REST endpoints for triggering workflows, monitoring tasks,
receiving webhooks, and visual browser streaming.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from redis import Redis

from api.routes import (
    ai_extract_router,
    flows_router,
    google_search_router,
    tasks_router,
    webhooks_router,
)
from core.celery_app import celery_app
from core.config import settings
from core.security import (
    InternalNetworkMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
    verify_internal_api_key,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("FastAPI Gateway initialized. Connecting to Redis: %s", settings.REDIS_URL)
    logger.info("Security Mode: Zero-Trust Container Auth (API Key: %s, Anti-SSRF: %s, Internal IP Filter: %s)",
                "ENABLED" if settings.REQUIRE_API_KEY else "DISABLED",
                "ENABLED" if settings.ENABLE_SSRF_PROTECTION else "DISABLED",
                "ENABLED" if settings.ENFORCE_INTERNAL_IP_ONLY else "DISABLED")
    yield
    logger.info("FastAPI Gateway shutting down.")


app = FastAPI(
    title="Omni-Flow Scraping & AI Content Extraction Gateway",
    description=(
        "Enterprise-grade Python automation orchestrator and AI Web Reader. "
        "Transforms any website into clean LLM-optimized Markdown with token metrics, "
        "anti-bot stealth, virtual displays via noVNC, and distributed Celery Canvas pipelines. "
        "Secured for zero-trust internal container-to-container communication."
    ),
    version="1.2.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# 1. Security Headers & Server Identity Masking
app.add_middleware(SecurityHeadersMiddleware)

# 2. Maximum Payload Size Limiter (DoS Protection)
app.add_middleware(
    RequestSizeLimitMiddleware,
    max_size_bytes=settings.MAX_REQUEST_SIZE_BYTES,
)

# 3. Restrict Access to Internal Docker Networks and Localhost (Defense-in-depth)
app.add_middleware(
    InternalNetworkMiddleware,
    allowed_cidrs=settings.allowed_cidrs_list,
    enabled=settings.ENFORCE_INTERNAL_IP_ONLY,
)

# 4. CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers with Zero-Trust Container API Key Authentication
app.include_router(
    ai_extract_router,
    dependencies=[Depends(verify_internal_api_key)],
)
app.include_router(
    flows_router,
    prefix="/api/v1",
    dependencies=[Depends(verify_internal_api_key)],
)
app.include_router(
    tasks_router,
    prefix="/api/v1",
    dependencies=[Depends(verify_internal_api_key)],
)
app.include_router(
    webhooks_router,
    prefix="/api/v1",
    dependencies=[Depends(verify_internal_api_key)],
)
app.include_router(
    google_search_router,
    dependencies=[Depends(verify_internal_api_key)],
)


@app.get("/", tags=["General"], dependencies=[Depends(verify_internal_api_key)])
def root(request: Request):
    host = request.base_url.hostname or "localhost"
    return {
        "name": "Omni-Flow Scraping & AI Content Extractor",
        "status": "ONLINE",
        "documentation": "/docs",
        "ai_reader_example": f"http://{host}:8000/r/https://github.com/torvalds/linux",
        "vnc_live_streams": {
            "worker_1": f"http://{host}:6081/vnc.html?autoconnect=true",
            "worker_2": f"http://{host}:6082/vnc.html?autoconnect=true",
        },
        "monitoring": {
            "celery_flower": f"http://{host}:5555",
        },
    }


@app.get("/health", tags=["Health"])
def health():
    """Health check validating Redis connection and Celery worker availability."""
    redis_healthy = False
    active_workers = []

    try:
        r = Redis.from_url(settings.REDIS_URL, socket_timeout=2)
        redis_healthy = bool(r.ping())
    except Exception as e:
        logger.warning("Redis health check failed: %s", e)

    try:
        inspector = celery_app.control.inspect(timeout=1.0)
        active = inspector.ping()
        if active:
            active_workers = list(active.keys())
    except Exception as e:
        logger.warning("Celery inspect ping failed: %s", e)

    return {
        "status": "HEALTHY" if redis_healthy else "DEGRADED",
        "redis_connected": redis_healthy,
        "active_celery_workers": active_workers,
    }


@app.get("/api/v1/vnc-info", tags=["Monitoring"], dependencies=[Depends(verify_internal_api_key)])
def vnc_info(request: Request):
    """Direct links to the real-time visual streaming of each scraping worker."""
    host = request.base_url.hostname or "localhost"
    return {
        "description": "Access these URLs directly in your browser to view the Chromium browser in real-time.",
        "worker_1_novnc": f"http://{host}:6081/vnc.html?autoconnect=true",
        "worker_2_novnc": f"http://{host}:6082/vnc.html?autoconnect=true",
    }
