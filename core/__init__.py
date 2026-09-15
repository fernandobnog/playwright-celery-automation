"""Core module containing configuration and Celery instance."""
from core.config import settings
from core.celery_app import celery_app
from core.utils import normalize_url

__all__ = ["settings", "celery_app", "normalize_url"]
