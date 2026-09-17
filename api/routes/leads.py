"""
FastAPI Routes for Lead Capture and One-Click WhatsApp Approval.
Receives website contact form events and handles human-in-the-loop approvals.
"""

import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from core.security import verify_approval_token
from flows.flow_lead_qualification import task_process_lead_qualification
from integrations.evolution import EvolutionClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leads", tags=["Leads & CRM"])


class LeadContactPayload(BaseModel):
    name: str = Field(description="Full name of the contact")
    email: str = Field(description="Primary email address")
    phone: Optional[str] = Field(default="", description="Contact phone number")
    phoneDigits: Optional[str] = Field(default="", description="Raw phone digits")
    subject: Optional[str] = Field(default="Novo Contato", description="Form subject")
    message: Optional[str] = Field(default="", description="User message text")
    formattedMessage: Optional[str] = Field(default=None, description="Preformatted message for AI audit")


@router.post("/contact-form", status_code=status.HTTP_202_ACCEPTED)
def receive_contact_form(payload: LeadContactPayload) -> Dict[str, Any]:
    """
    Receives contact form submission from the personal website.
    Dispatches asynchronous qualification, CRM registration, and notification pipeline.
    """
    logger.info("Received lead contact form for %s (%s)", payload.name, payload.email)
    async_task = task_process_lead_qualification.delay(payload.model_dump())
    return {
        "status": "ACCEPTED",
        "task_id": async_task.id,
        "message": "Lead qualification pipeline scheduled.",
    }


@router.get("/approve", response_class=HTMLResponse)
async def approve_whatsapp_message(
    token: str = Query(..., description="Signed one-click approval token")
):
    """
    Human-in-the-loop approval endpoint.
    Validates token signature and sends automated WhatsApp greeting to the lead.
    """
    payload = verify_approval_token(token)
    if not payload:
        return HTMLResponse(
            content="""
            <!DOCTYPE html>
            <html>
            <head><meta charset="utf-8"><title>Link Inválido</title></head>
            <body style="font-family: sans-serif; text-align: center; padding: 50px; background: #fff1f2; color: #9f1239;">
                <h2>❌ Link expirado ou inválido</h2>
                <p>Este link de aprovação não é mais válido ou já expirou.</p>
            </body>
            </html>
            """,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    phone = payload.get("phone")
    first_name = payload.get("first_name", "")
    message_text = f"Oi, {first_name}!\nVi seu contato no site.\nPosso te passar os detalhes por aqui mesmo?"

    evo = EvolutionClient()
    try:
        await evo.send_text_message(phone=phone, text=message_text)
        logger.info("WhatsApp greeting successfully approved and sent to %s (%s)", first_name, phone)
    except Exception as e:
        logger.error("Failed to send WhatsApp greeting to %s: %s", phone, e)
        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html>
            <head><meta charset="utf-8"><title>Erro no Envio</title></head>
            <body style="font-family: sans-serif; text-align: center; padding: 50px; background: #fff1f2; color: #9f1239;">
                <h2>⚠️ Falha no envio da mensagem</h2>
                <p>Ocorreu um erro ao enviar a mensagem no WhatsApp: {e}</p>
            </body>
            </html>
            """,
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    return HTMLResponse(
        content=f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>WhatsApp Aprovado</title>
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f8fafc; padding: 40px 20px; text-align: center; color: #1e293b;">
            <div style="max-width: 480px; margin: 0 auto; background: #ffffff; padding: 32px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); border: 1px solid #e2e8f0;">
                <div style="font-size: 48px; margin-bottom: 16px;">✅</div>
                <h2 style="color: #0f172a; margin: 0 0 12px 0;">Mensagem Enviada!</h2>
                <p style="color: #475569; font-size: 15px; line-height: 1.5;">
                    O WhatsApp foi disparado com sucesso para <strong>{first_name}</strong> ({phone}).
                </p>
                <div style="background: #f1f5f9; padding: 14px; border-radius: 8px; margin-top: 20px; font-size: 13px; color: #334155; text-align: left;">
                    <em>"{message_text.replace(chr(10), '<br>')}"</em>
                </div>
            </div>
        </body>
        </html>
        """
    )
