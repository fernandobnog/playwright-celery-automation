"""Core module containing configuration and Celery instance."""
from core.config import settings
from core.celery_app import celery_app

__all__ = ["settings", "celery_app"]
