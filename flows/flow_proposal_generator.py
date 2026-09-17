"""
Flow: Proposal & Contract Generator (Google Docs -> PDF -> WhatsApp / Email).
Generates customized musical proposals in Google Docs, exports directly to PDF via Drive,
and automatically delivers them to the client via WhatsApp (Evolution API) and Email (Gmail API).
"""

import asyncio
import base64
from datetime import datetime
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from core.celery_app import celery_app
from core.config import settings
from integrations.evolution import EvolutionClient, format_brazilian_phone
from integrations.google import GoogleHub
from storage.repository import repo

logger = logging.getLogger(__name__)


class ProposalRequest(BaseModel):
    client_name: str = Field(..., description="Nome do contratante ou responsável pelo local")
    client_phone: str = Field(..., description="Telefone / WhatsApp do cliente")
    event_date: str = Field(..., description="Data do evento (ex: 15/11/2026)")
    event_time: str = Field(default="20:00 às 23:00", description="Horário de início e término")
    venue_name: str = Field(..., description="Nome do espaço, bar ou evento")
    venue_city: str = Field(default="Mogi Mirim / SP", description="Cidade e estado do evento")
    cache_value: str = Field(..., description="Valor acordado ou proposto (ex: R$ 600,00)")
    repertoire_style: str = Field(default="MPB, Pop Rock Nacional/Internacional e Clássicos", description="Gêneros do show")
    observations: str = Field(
        default="Sonorização própria inclusa para até 150 pessoas. Alimentação e bebidas inclusas.",
        description="Condições técnicas e observações",
    )
    template_id: Optional[str] = Field(default=None, description="ID opcional de template no Google Drive")
    send_whatsapp: bool = Field(default=True, description="Disparar PDF automaticamente no WhatsApp do cliente")
    send_email: Optional[str] = Field(default=None, description="E-mail opcional para envio simultâneo")


def generate_proposal_pdf_and_send(
    req: ProposalRequest,
    hub: Optional[GoogleHub] = None,
    evolution: Optional[EvolutionClient] = None,
    output_dir: str = "/app/downloads/propostas",
) -> Dict[str, Any]:
    """
    Executes Google Docs instantiation, PDF exportation, and WhatsApp/Email dispatch.
    """
    google_hub = hub or GoogleHub()
    evo = evolution or EvolutionClient()

    logger.info("Generating proposal for %s at %s (%s)...", req.client_name, req.venue_name, req.event_date)
    title = f"Proposta Show - {req.client_name} - {req.event_date.replace('/', '-')}"

    # 1. Create or clone Document
    if req.template_id:
        copied = google_hub.drive.copy_file(req.template_id, new_title=title)
        doc_id = copied.get("id")
    else:
        doc = google_hub.docs.create_document(title)
        doc_id = doc.get("documentId")
        # Populate structured layout
        proposal_body = (
            "==========================================================\n"
            "   FERNANDO NOGUEIRA — MÚSICA AO VIVO (VOZ E VIOLÃO)\n"
            "           PROPOSTA COMERCIAL DE APRESENTAÇÃO\n"
            "==========================================================\n\n"
            "1. DADOS DO CONTRATANTE E EVENTO\n"
            "----------------------------------------------------------\n"
            f"• Contratante / Responsável: {{{{nome_cliente}}}}\n"
            f"• Espaço / Local do Show: {{{{local}}}} ({{{{cidade}}}})\n"
            f"• Data da Apresentação: {{{{data_evento}}}}\n"
            f"• Horário do Show: {{{{horario}}}}\n\n"
            "2. ESPECIFICAÇÕES ARTÍSTICAS & TÉCNICAS\n"
            "----------------------------------------------------------\n"
            f"• Formato: Show solo acústico (Voz e Violão profissional)\n"
            f"• Estilo do Repertório: {{{{repertorio}}}}\n"
            f"• Equipamento: Sistema de som próprio adequado para o ambiente\n"
            f"• Observações Técnicas: {{{{observacoes}}}}\n\n"
            "3. INVESTIMENTO & CONDIÇÕES\n"
            "----------------------------------------------------------\n"
            f"• Valor do Cachê: {{{{valor_cache}}}}\n"
            "• Forma de Pagamento: PIX ou Transferência bancária no término do show\n"
            "• Validade desta Proposta: 7 dias a contar da data de emissão\n\n"
            "----------------------------------------------------------\n"
            "Atenciosamente,\n\n"
            "Fernando Nogueira\n"
            "Músico e Produtor Musical | (19) 99825-6557\n"
            "www.fernandonogueira.dev.br\n"
        )
        google_hub.docs.append_text(doc_id, proposal_body)

    # 2. Replace placeholders in batch
    replacements = {
        "nome_cliente": req.client_name,
        "local": req.venue_name,
        "cidade": req.venue_city,
        "data_evento": req.event_date,
        "horario": req.event_time,
        "repertorio": req.repertoire_style,
        "valor_cache": req.cache_value,
        "observacoes": req.observations,
    }
    google_hub.docs.replace_text(doc_id, replacements)

    # 3. Export to PDF
    out_dir_path = Path(output_dir)
    if not out_dir_path.exists():
        out_dir_path = Path("downloads/propostas")
    out_dir_path.mkdir(parents=True, exist_ok=True)

    sanitized_name = req.client_name.strip().replace(" ", "_")
    pdf_filename = f"Proposta_Show_{sanitized_name}_{req.event_date.replace('/', '-')}.pdf"
    pdf_file_path = out_dir_path / pdf_filename

    pdf_bytes = google_hub.drive.export_as_pdf(doc_id, output_file_path=str(pdf_file_path))
    logger.info("PDF exported successfully (%d bytes): %s", len(pdf_bytes), pdf_file_path)

    delivery_status = {"whatsapp": "SKIPPED", "email": "SKIPPED"}

    # 4. Dispatch via WhatsApp
    if req.send_whatsapp:
        try:
            phone = format_brazilian_phone(req.client_phone)
            pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
            caption = (
                f"Olá {req.client_name}! Tudo bem?\n\n"
                f"Conforme conversamos, segue em anexo a proposta detalhada para o nosso show no dia *{req.event_date}* "
                f"no *{req.venue_name}*.\n\n"
                "Fico à total disposição para alinharmos os detalhes e confirmarmos a data na agenda!\n\n"
                "Grande abraço,\n*Fernando Nogueira*"
            )

            res_evo = asyncio.run(
                evo.send_media_message(
                    phone=phone,
                    media_base64_or_url=pdf_base64,
                    file_name=pdf_filename,
                    caption=caption,
                    media_type="document",
                    mime_type="application/pdf",
                )
            )
            delivery_status["whatsapp"] = "SENT"
            logger.info("Proposal PDF successfully dispatched via WhatsApp to %s", phone)
        except Exception as err_wpp:
            delivery_status["whatsapp"] = f"ERROR: {err_wpp}"
            logger.error("Failed to send WhatsApp proposal to %s: %s", req.client_phone, err_wpp)

    # 5. Dispatch via Email
    if req.send_email:
        try:
            email_subject = f"🎵 Proposta de Show - Fernando Nogueira ({req.event_date})"
            email_body = (
                f"<p>Olá <strong>{req.client_name}</strong>,</p>"
                f"<p>Segue em anexo a proposta para a apresentação musical no dia <strong>{req.event_date}</strong> no <strong>{req.venue_name}</strong>.</p>"
                "<p>Qualquer dúvida, estou à disposição no WhatsApp (19) 99825-6557.</p>"
                "<p>Atenciosamente,<br><strong>Fernando Nogueira</strong><br><a href='https://www.fernandonogueira.dev.br'>www.fernandonogueira.dev.br</a></p>"
            )
            google_hub.gmail.send_email(
                to_email=req.send_email,
                subject=email_subject,
                html_body=email_body,
                attachments=[{
                    "filename": pdf_filename,
                    "content": pdf_bytes,
                    "mimetype": "application/pdf",
                }]
            )
            delivery_status["email"] = "SENT"
            logger.info("Proposal PDF successfully dispatched via Email to %s", req.send_email)
        except Exception as err_mail:
            delivery_status["email"] = f"ERROR: {err_mail}"
            logger.error("Failed to send Email proposal to %s: %s", req.send_email, err_mail)

    return {
        "status": "SUCCESS",
        "document_id": doc_id,
        "pdf_filename": pdf_filename,
        "pdf_path": str(pdf_file_path),
        "delivery": delivery_status,
        "generated_at": datetime.now().isoformat(),
    }


@celery_app.task(name="flows.flow_proposal_generator.task_generate_and_send_proposal")
def task_generate_and_send_proposal(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Celery task that coordinates proposal generation and automated delivery.
    """
    task_id = "proposal_" + datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    repo.log_flow_start(task_id, "proposal_generator", request_data)

    req = ProposalRequest(**request_data)
    result = generate_proposal_pdf_and_send(req)

    repo.log_flow_complete(task_id, result)
    return result
