"""
API Routes for generating and dispatching musical proposals and contracts in PDF.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from flows.flow_proposal_generator import ProposalRequest, task_generate_and_send_proposal, generate_proposal_pdf_and_send

router = APIRouter(prefix="/proposals", tags=["Proposals & Contracts"])


@router.post("/generate")
def generate_proposal_endpoint(
    payload: ProposalRequest,
    run_async: bool = Query(default=True, description="Whether to enqueue in Celery (True) or run synchronously (False)"),
):
    """
    Generates a formal proposal in Google Docs, converts directly to PDF,
    and dispatches via WhatsApp (Evolution API) and/or Email (Gmail API).
    """
    try:
        if run_async:
            task = task_generate_and_send_proposal.delay(payload.model_dump())
            return {
                "status": "QUEUED",
                "task_id": task.id,
                "message": "Proposal generation enqueued in Celery. Delivery to WhatsApp/Email will be performed automatically.",
                "status_url": f"/api/v1/tasks/{task.id}",
            }
        else:
            result = generate_proposal_pdf_and_send(payload)
            return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate proposal: {e}")
