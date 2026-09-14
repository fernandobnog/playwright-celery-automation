"""
Application Configuration Module.
Centralizes environment settings using Pydantic Settings.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Redis & Celery
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/1"
    CELERY_CONCURRENCY: int = 2

    # Playwright & Virtual Display
    PLAYWRIGHT_HEADLESS: bool = False
    PLAYWRIGHT_USER_DATA_DIR: str = "/app/data/browser_profile"
    PLAYWRIGHT_TIMEOUT_MS: int = 30000
    DISPLAY: str = ":99"
    XVFB_RESOLUTION: str = "1920x1080x24"
    VNC_PORT: int = 5900
    NOVNC_PORT: int = 6080

    # API Settings
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    ENVIRONMENT: str = "production"

    # Paths & Storage
    DATABASE_URL: str = "sqlite:////app/data/pipeline.db"
    DOWNLOADS_DIR: str = "/app/downloads"
    DATA_DIR: str = "/app/data"

    # Webhook Resilience
    WEBHOOK_RETRY_MAX: int = 3
    WEBHOOK_TIMEOUT: int = 15

    @property
    def downloads_path(self) -> Path:
        p = Path(self.DOWNLOADS_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def data_path(self) -> Path:
        p = Path(self.DATA_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
