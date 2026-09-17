"""
FastAPI Routes for Human-in-the-Loop Editorial Topic Selection and Content Triggering.
Receives one-click callback from daily curation email and initiates deep research & drafting pipeline.
"""

import html
import logging
from typing import Any, Dict
from fastapi import APIRouter, Query, status
from fastapi.responses import HTMLResponse

from core.security import verify_editorial_action_token
from flows.flow_content_deep_writer import task_deep_content_generation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/editorial", tags=["Editorial & Content Ops"])


@router.get("/select", response_class=HTMLResponse)
async def select_editorial_topic(
    token: str = Query(..., description="Signed HMAC token from the daily email button")
):
    """
    One-click callback endpoint triggered when clicking 'Gerar Conteúdo' in the daily email.
    Validates token and enqueues deep research & writing pipeline in Celery.
    """
    payload = verify_editorial_action_token(token)
    if not payload:
        logger.warning("Invalid or expired editorial action token presented.")
        return HTMLResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content="""
            <!DOCTYPE html>
            <html lang="pt-BR">
            <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Link Inválido ou Expirado</title>
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 16px; }
                    .card { background-color: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 32px; max-width: 480px; text-align: center; box-shadow: 0 10px 25px rgba(0,0,0,0.3); }
                    .icon { font-size: 44px; margin-bottom: 16px; }
                    h1 { font-size: 20px; margin: 0 0 10px 0; color: #f43f5e; }
                    p { color: #94a3b8; font-size: 14px; line-height: 1.6; margin: 0; }
                </style>
            </head>
            <body>
                <div class="card">
                    <div class="icon">⚠️</div>
                    <h1>Link Expirado ou Inválido</h1>
                    <p>Este link de ação expirou (validade de 48h) ou possui uma assinatura inválida. Por favor, verifique o e-mail de curadoria mais recente.</p>
                </div>
            </body>
            </html>
            """,
        )

    pauta_titulo = payload.get("pauta_titulo", "Tema Selecionado")
    categoria = payload.get("categoria", "Tecnologia da Informação")
    target_format = payload.get("target_format", "both")

    logger.info("Editorial topic selected: '%s' [%s]. Queuing Celery pipeline...", pauta_titulo, categoria)
    async_task = task_deep_content_generation.delay(payload)

    escaped_title = html.escape(pauta_titulo)
    escaped_cat = html.escape(categoria)

    return HTMLResponse(
        content=f"""
        <!DOCTYPE html>
        <html lang="pt-BR">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Tema Selecionado com Sucesso</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    background-color: #0f172a;
                    color: #f8fafc;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-height: 100vh;
                    margin: 0;
                    padding: 16px;
                }}
                .card {{
                    background-color: #1e293b;
                    border: 1px solid #334155;
                    border-radius: 12px;
                    padding: 32px 28px;
                    max-width: 520px;
                    text-align: center;
                    box-shadow: 0 20px 35px rgba(0,0,0,0.4);
                }}
                .badge {{
                    display: inline-block;
                    background-color: rgba(99, 102, 241, 0.2);
                    color: #818cf8;
                    font-size: 11px;
                    font-weight: 700;
                    letter-spacing: 0.8px;
                    padding: 4px 10px;
                    border-radius: 20px;
                    text-transform: uppercase;
                    margin-bottom: 12px;
                }}
                h1 {{
                    font-size: 22px;
                    font-weight: 800;
                    margin: 0 0 12px 0;
                    color: #ffffff;
                }}
                .pauta-box {{
                    background-color: rgba(15, 23, 42, 0.6);
                    border: 1px solid #334155;
                    border-radius: 8px;
                    padding: 16px 18px;
                    margin: 20px 0;
                    text-align: left;
                }}
                .pauta-title {{
                    color: #38bdf8;
                    font-size: 15px;
                    font-weight: 700;
                    line-height: 1.4;
                    margin: 0 0 6px 0;
                }}
                .status-line {{
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    color: #4ade80;
                    font-size: 13px;
                    font-weight: 600;
                }}
                .pulse-dot {{
                    width: 8px;
                    height: 8px;
                    background-color: #4ade80;
                    border-radius: 50%;
                    box-shadow: 0 0 10px #4ade80;
                    animation: pulse 1.5s infinite;
                }}
                @keyframes pulse {{
                    0% {{ opacity: 0.4; }}
                    50% {{ opacity: 1; }}
                    100% {{ opacity: 0.4; }}
                }}
                p.info {{
                    color: #94a3b8;
                    font-size: 13px;
                    line-height: 1.6;
                    margin: 0;
                }}
            </style>
        </head>
        <body>
            <div class="card">
                <span class="badge">🚀 Pipeline Editorial Iniciado</span>
                <h1>Tema Selecionado com Sucesso!</h1>
                
                <div class="pauta-box">
                    <div class="pauta-title">{escaped_title}</div>
                    <div class="status-line">
                        <span class="pulse-dot"></span>
                        <span>Agente redator em execução (Task: {async_task.id[:8]}...)</span>
                    </div>
                </div>

                <p class="info">
                    O Gemini IA está aprofundando o assunto, elaborando o <strong>Artigo de Blog</strong> (com SEO) e o <strong>Post de LinkedIn</strong>.<br><br>
                    Assim que estiver concluído (~60 segundos), você receberá o link do <strong>Google Docs</strong> diretamente no seu WhatsApp e no seu E-mail.
                </p>
            </div>
        </body>
        </html>
        """
    )
