"""
Celery Application Factory and Configuration.
Defines broker, result backend, task routing, queues, and beat schedule.
"""

from celery import Celery
from celery.schedules import crontab
from kombu import Queue

from core.config import settings

celery_app = Celery(
    "automation_pipeline",
    broker=settings.REDIS_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Sao_Paulo",
    enable_utc=True,

    # Task execution settings
    task_track_started=True,
    task_time_limit=300,        # Hard timeout: 5 minutes
    task_soft_time_limit=240,   # Soft timeout: 4 minutes
    result_expires=86400,       # Keep results for 24 hours

    # Concurrency and prefetching
    worker_prefetch_multiplier=1, # Important for heavy scraping: 1 task per worker at a time
    task_acks_late=True,          # Acknowledge only after task completes
    task_reject_on_worker_lost=True,

    # Queues definition
    task_queues=(
        Queue("scraping", routing_key="scraping.#"),
        Queue("flows", routing_key="flows.#"),
        Queue("default", routing_key="default.#"),
    ),
    task_default_queue="default",

    # Routing
    task_routes={
        "flows.tasks_etl.task_scrape_quotes": {"queue": "scraping"},
        "flows.tasks_etl.task_scrape_page": {"queue": "scraping"},
        "flows.tasks_etl.task_transform_quotes": {"queue": "flows"},
        "flows.tasks_etl.task_persist_quotes": {"queue": "flows"},
        "flows.tasks_etl.task_dispatch_webhook": {"queue": "flows"},
        "flows.tasks_etl.task_flow_failure_handler": {"queue": "flows"},
        "flows.tasks_ai_extract.task_ai_extract_url": {"queue": "scraping"},
        "flows.tasks_google_search.task_google_search": {"queue": "scraping"},
        "flows.flow_link_monitor.task_check_network_links": {"queue": "flows"},
        "flows.flow_lead_qualification.task_process_lead_qualification": {"queue": "flows"},
        "flows.flow_editorial_pautas.task_daily_editorial_curation": {"queue": "flows"},
        "flows.flow_calendar_sync.task_sync_sheets_to_calendar": {"queue": "flows"},
    },

    # Beat Periodic Schedules (Automations replacing n8n scheduled triggers)
    beat_schedule={
        "check-links-nt-periodic": {
            "task": "flows.flow_link_monitor.task_check_network_links",
            "schedule": crontab(minute="*"),  # Every minute (Check Links NT)
            "options": {"queue": "flows"},
        },
        "calendar-sync-periodic": {
            "task": "flows.flow_calendar_sync.task_sync_sheets_to_calendar",
            "schedule": crontab(minute="*/30"),  # Every 30 minutes (Add evento na agenda)
            "options": {"queue": "flows"},
        },
        "daily-editorial-pautas-07am": {
            "task": "flows.flow_editorial_pautas.task_daily_editorial_curation",
            "schedule": crontab(hour=7, minute=0),  # Every day at 07:00 AM (Temas em alta)
            "options": {"queue": "flows"},
        },
        "daily-morning-quote-etl": {
            "task": "flows.example_flow.trigger_scheduled_quote_etl",
            "schedule": crontab(hour=8, minute=0),  # Everyday at 08:00 AM
            "args": ("https://quotes.toscrape.com", "inspirational", 5),
            "options": {"queue": "flows"},
        },
        "hourly-quick-check": {
            "task": "flows.tasks_etl.task_periodic_health_heartbeat",
            "schedule": crontab(minute=0),  # Hourly
            "options": {"queue": "default"},
        },
    },
    imports=[
        "flows.tasks",
        "flows.tasks_etl",
        "flows.tasks_ai_extract",
        "flows.tasks_google_search",
        "flows.example_flow",
        "flows.parallel_flow",
        "flows.flow_link_monitor",
        "flows.flow_lead_qualification",
        "flows.flow_editorial_pautas",
        "flows.flow_calendar_sync",
    ],
)

# Auto-discover tasks in packages
celery_app.autodiscover_tasks(["flows"])
