"""
API Routes for querying Celery asynchronous task status and results.
"""

from celery.result import AsyncResult
from fastapi import APIRouter
from api.schemas.requests import TaskStatusResponse
from core.celery_app import celery_app

router = APIRouter(prefix="/tasks", tags=["Task Monitoring"])


@router.get("/{task_id}", response_model=TaskStatusResponse)
def get_task_status(task_id: str):
    """
    Checks the status and output of any running or completed task in the pipeline.
    """
    task_res = AsyncResult(task_id, app=celery_app)

    response = TaskStatusResponse(
        task_id=task_id,
        status=task_res.status,
        ready=task_res.ready(),
    )

    if task_res.ready():
        response.successful = task_res.successful()
        if task_res.successful():
            response.result = task_res.result
        else:
            response.error = str(task_res.result)
    elif task_res.status == "STARTED":
        response.result = {"info": "Task is actively running on a worker"}

    return response
