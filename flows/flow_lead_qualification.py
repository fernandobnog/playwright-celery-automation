"""
Flow: Twenty CRM - Lead Form Qualification & Automated Human-in-the-Loop Workflow.
Receives website contact leads, runs AI security qualification (Gemini), records into Twenty CRM,
and initiates WhatsApp contact verification with one-click approval.
"""

import asyncio
import html
import logging
from typing import Any, Dict, Optional
from core.celery_app import celery_app

from core.config import settings
from core.security import create_approval_token
from integrations.evolution import EvolutionClient, format_brazilian_phone
from integrations.gemini import GeminiClient, LeadAuditResult
from integrations.google_service import GoogleServicesClient
from integrations.twenty import TwentyCRMClient
from storage.repository import repo

logger = logging.getLogger(__name__)

AUDIT_SYSTEM_INSTRUCTION = """
Você é um auditor sênior de segurança e qualificação de leads com foco estrito no mercado brasileiro.

Sua função é analisar payloads contendo WhatsApp, E-mail e Mensagem para definir se o lead deve ser aprovado para atendimento ou rejeitado.

Categorias de Status:
1. REAL: Contato legítimo de pessoa/empresa brasileira com interesse real, DDD e celular brasileiro plausíveis, e-mail válido e texto natural em pt-BR.
2. GOLPE: Phishing, links maliciosos, golpes financeiros, tentativas de invasão ou engenharia social.
3. FAKE: Mensagens de teste (contendo "teste", "webhook", "n8n", etc.), bots, spam/prospecção ativa para você, números sequenciais/fictícios, ou mensagens fora do padrão pt-BR.

Regra Estrita para o Campo Booleano:
- "aprovado": true SOMENTE se o status for "REAL" e todas as validações forem positivas.
- "aprovado": false se for GOLPE, FAKE, teste técnico, bot, idioma estrangeiro ou dados inconsistentes.
"""


def parse_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split(" ")
    first = parts[0] if parts else ""
    last = " ".join(parts[1:]) if len(parts) > 1 else ""
    return first, last


def generate_approval_email_html(
    name: str,
    email: str,
    phone: str,
    subject: str,
    message: str,
    approve_url: str,
) -> str:
    esc_name = html.escape(name or "")
    esc_email = html.escape(email or "")
    esc_phone = html.escape(phone or "")
    esc_subject = html.escape(subject or "")
    esc_message = html.escape(message or "").replace("\n", "<br>")
    first_name_esc = html.escape(name.split()[0] if name else "")

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #f8fafc; padding: 20px; color: #1e293b;">
        <div style="max-width: 560px; margin: 0 auto; background: #ffffff; border-radius: 8px; padding: 24px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border: 1px solid #e2e8f0;">
            <h2 style="color: #0f172a; margin-top: 0; font-size: 20px;">⚡ Novo Lead Qualificado (Site Pessoal)</h2>
            <p style="color: #64748b; font-size: 14px;">A IA auditou e qualificou este contato como <strong>LEGÍTIMO (REAL)</strong>.</p>
            
            <table style="width: 100%; font-size: 14px; margin: 20px 0; border-collapse: collapse;">
                <tr><td style="padding: 6px 0; color: #64748b; width: 90px;"><strong>Nome:</strong></td><td>{esc_name}</td></tr>
                <tr><td style="padding: 6px 0; color: #64748b;"><strong>E-mail:</strong></td><td>{esc_email}</td></tr>
                <tr><td style="padding: 6px 0; color: #64748b;"><strong>WhatsApp:</strong></td><td>{esc_phone}</td></tr>
                <tr><td style="padding: 6px 0; color: #64748b;"><strong>Assunto:</strong></td><td>{esc_subject}</td></tr>
                <tr><td style="padding: 6px 0; color: #64748b; vertical-align: top;"><strong>Mensagem:</strong></td><td style="background: #f1f5f9; padding: 10px; border-radius: 6px;">{esc_message}</td></tr>
            </table>

            <div style="margin-top: 24px; text-align: center;">
                <a href="{approve_url}" style="display: inline-block; background-color: #16a34a; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 14px;">
                    ✅ Aprovar & Enviar Mensagem no WhatsApp
                </a>
            </div>
            <p style="color: #94a3b8; font-size: 12px; text-align: center; margin-top: 16px;">
                Link válido por 48 horas. Mensagem que será enviada: <em>"Oi, {first_name_esc}! Vi seu contato no site. Posso te passar os detalhes por aqui mesmo?"</em>
            </p>
        </div>
    </body>
    </html>
    """


@celery_app.task(bind=True, name="flows.flow_lead_qualification.task_process_lead_qualification")
def task_process_lead_qualification(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Asynchronous Celery pipeline replacing the n8n 'Twenty CRM - Lead Form' workflow.
    """
    task_id = self.request.id or "manual_task"
    logger.info("Starting lead qualification pipeline for task %s", task_id)
    repo.log_flow_start(task_id, "twenty_crm_lead_qualification", payload)

    name = payload.get("name", "").strip()
    email = payload.get("email", "").strip()
    phone = payload.get("phone") or payload.get("phoneDigits", "")
    subject = payload.get("subject", "Novo Contato")
    message = payload.get("message", "")
    formatted_msg = payload.get("formattedMessage") or f"Nome: {name}\nTel: {phone}\nEmail: {email}\nMensagem: {message}"

    # --------------------------------------------------------------------------
    # 1. AI Security Audit via Google Gemini
    # --------------------------------------------------------------------------
    gemini = GeminiClient()
    logger.info("Auditing lead with Gemini: %s (%s)", name, email)
    audit: LeadAuditResult = gemini.generate_structured(
        prompt=formatted_msg,
        system_instruction=AUDIT_SYSTEM_INSTRUCTION,
        response_model=LeadAuditResult,
    )

    if not audit.aprovado:
        logger.warning(
            "Lead rejected by Gemini AI audit! Status: %s, Motivo: %s",
            audit.status,
            audit.motivo,
        )
        result = {"status": "REJECTED", "audit": audit.model_dump()}
        repo.log_flow_complete(task_id, result)
        return result

    logger.info("Lead approved by Gemini AI! Score: %.2f", audit.score_confianca)

    # --------------------------------------------------------------------------
    # 2. Synchronize with Twenty CRM
    # --------------------------------------------------------------------------
    twenty = TwentyCRMClient()
    first_name, last_name = parse_name(name)

    async def _crm_ops():
        # A. Create Note
        note_title = f"Formulário: {subject}"
        note_id = await twenty.create_note(title=note_title, markdown_body=message)

        # B. Check existing person
        person = await twenty.find_person_by_email(email)
        if person:
            person_id = person["id"]
            logger.info("Found existing Twenty CRM Person ID %s for %s", person_id, email)
        else:
            person_id = await twenty.create_person(
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=phone,
            )
            logger.info("Created new Twenty CRM Person ID %s for %s", person_id, email)

        # C. Link Note to Person
        await twenty.link_note_to_person(note_id=note_id, person_id=person_id)
        return person_id, note_id

    person_id, note_id = asyncio.run(_crm_ops())

    # --------------------------------------------------------------------------
    # 3. Check WhatsApp availability & Dispatch Approval
    # --------------------------------------------------------------------------
    evo = EvolutionClient()
    has_whatsapp = False
    norm_phone = None

    try:
        norm_phone = format_brazilian_phone(phone)
        has_whatsapp = asyncio.run(evo.check_whatsapp_number(norm_phone))
        logger.info("Phone %s has WhatsApp: %s", norm_phone, has_whatsapp)
    except Exception as phone_err:
        logger.warning("Could not normalize/verify WhatsApp for phone %s: %s", phone, phone_err)

    approval_sent = False
    if has_whatsapp and norm_phone:
        token = create_approval_token(phone=norm_phone, first_name=first_name)
        approve_url = f"https://www.fernandonogueira.dev.br/api/v1/leads/approve?token={token}"

        # Send interactive approval email via GoogleServicesClient
        try:
            google_svc = GoogleServicesClient()
            email_html = generate_approval_email_html(
                name=name,
                email=email,
                phone=norm_phone,
                subject=subject,
                message=message,
                approve_url=approve_url,
            )
            google_svc.send_email(
                to_email=settings.ADMIN_EMAIL,
                subject=f"Novo Lead Qualificado: {name}",
                html_body=email_html,
            )
            approval_sent = True
            logger.info("Approval email sent to %s", settings.ADMIN_EMAIL)
        except Exception as email_err:
            logger.error("Failed to send approval email: %s", email_err)

        # Also dispatch instant WhatsApp ping to Fernando
        try:
            asyncio.run(
                evo.send_text_message(
                    phone=settings.NOTIFICATION_PHONE,
                    text=f"Novo lead no site:\nNome: {name}\nEmail: {email}\nTel: {norm_phone}\n\nAcesse o e-mail ou clique para aprovar envio no WhatsApp:\n{approve_url}",
                )
            )
        except Exception as evo_err:
            logger.warning("Failed to send WhatsApp alert to admin: %s", evo_err)

    final_result = {
        "status": "APPROVED",
        "person_id": person_id,
        "note_id": note_id,
        "has_whatsapp": has_whatsapp,
        "approval_dispatched": approval_sent,
        "audit": audit.model_dump(),
    }
    repo.log_flow_complete(task_id, final_result)
    return final_result
