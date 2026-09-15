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

    # Security & Zero-Trust Container Isolation
    INTERNAL_API_KEY: str | None = None
    REQUIRE_API_KEY: bool = True
    ENFORCE_INTERNAL_IP_ONLY: bool = True
    ALLOWED_CIDRS: str = "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    ENABLE_SSRF_PROTECTION: bool = True
    MAX_REQUEST_SIZE_BYTES: int = 5 * 1024 * 1024  # 5MB
    FLOWER_BASIC_AUTH: str | None = None

    @property
    def allowed_cidrs_list(self) -> list[str]:
        if not self.ALLOWED_CIDRS:
            return []
        return [c.strip() for c in self.ALLOWED_CIDRS.split(",") if c.strip()]

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
