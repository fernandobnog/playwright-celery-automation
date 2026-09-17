"""
API Routes for manually triggering Google Sheets to PostgreSQL site syncs.
"""

from fastapi import APIRouter, HTTPException, Query
from flows.flow_site_sync import task_sync_site_agenda, task_sync_site_repertorio, sync_agenda_to_postgres, sync_repertorio_to_postgres

router = APIRouter(prefix="/sync", tags=["Site Data Synchronization"])


@router.post("/agenda")
def sync_agenda_endpoint(
    run_async: bool = Query(default=True, description="Whether to enqueue in Celery (True) or run synchronously (False)"),
):
    """
    Synchronizes the public shows agenda from Google Sheets into site-postgres.
    """
    try:
        if run_async:
            task = task_sync_site_agenda.delay()
            return {
                "status": "QUEUED",
                "task_id": task.id,
                "message": "Agenda synchronization task enqueued in Celery.",
                "status_url": f"/api/v1/tasks/{task.id}",
            }
        else:
            return sync_agenda_to_postgres()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to synchronize agenda: {e}")


@router.post("/repertorio")
def sync_repertorio_endpoint(
    run_async: bool = Query(default=True, description="Whether to enqueue in Celery (True) or run synchronously (False)"),
):
    """
    Synchronizes the music repertoire from Google Sheets into site-postgres.
    """
    try:
        if run_async:
            task = task_sync_site_repertorio.delay()
            return {
                "status": "QUEUED",
                "task_id": task.id,
                "message": "Repertoire synchronization task enqueued in Celery.",
                "status_url": f"/api/v1/tasks/{task.id}",
            }
        else:
            return sync_repertorio_to_postgres()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to synchronize repertoire: {e}")
