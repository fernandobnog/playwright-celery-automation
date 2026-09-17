"""
FastAPI Routes for Human-in-the-Loop Editorial Topic Selection and Content Triggering.
Receives one-click callback from daily curation email and initiates deep research & drafting pipeline.
Enforces single-topic selection per daily curation batch with conflict detection and idempotency locks.
"""

from datetime import datetime
import html
import json
import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, Query, status
from fastapi.responses import HTMLResponse
from redis import Redis

from core.config import settings
from core.security import verify_editorial_action_token
from flows.flow_content_deep_writer import task_deep_content_generation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/editorial", tags=["Editorial & Content Ops"])


def _get_redis() -> Optional[Redis]:
    """Returns connected Redis client or None if unreachable."""
    try:
        r = Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        r.ping()
        return r
    except Exception as e:
        logger.warning("Could not connect to Redis for editorial idempotency lock: %s", e)
        return None


def _render_invalid_token_html() -> str:
    return """
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
    """


def _render_already_in_progress_html(escaped_title: str, escaped_cat: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Tema Já em Produção</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 16px; }}
            .card {{ background-color: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 32px 28px; max-width: 520px; text-align: center; box-shadow: 0 20px 35px rgba(0,0,0,0.4); }}
            .badge {{ display: inline-block; background-color: rgba(34, 197, 94, 0.2); color: #4ade80; font-size: 11px; font-weight: 700; letter-spacing: 0.8px; padding: 4px 10px; border-radius: 20px; text-transform: uppercase; margin-bottom: 12px; }}
            h1 {{ font-size: 22px; font-weight: 800; margin: 0 0 12px 0; color: #ffffff; }}
            .pauta-box {{ background-color: rgba(15, 23, 42, 0.6); border: 1px solid #334155; border-radius: 8px; padding: 16px 18px; margin: 20px 0; text-align: left; }}
            .pauta-title {{ color: #38bdf8; font-size: 15px; font-weight: 700; line-height: 1.4; margin: 0 0 6px 0; }}
            p.info {{ color: #94a3b8; font-size: 13px; line-height: 1.6; margin: 0; }}
        </style>
    </head>
    <body>
        <div class="card">
            <span class="badge">✅ Tema Já em Produção</span>
            <h1>Tema Já Selecionado Anteriormente</h1>
            
            <div class="pauta-box">
                <div class="pauta-title">{escaped_title}</div>
                <div style="color: #64748b; font-size: 12px;">Categoria: {escaped_cat}</div>
            </div>

            <p class="info">
                Você já havia acionado a produção deste tema.<br><br>
                O agente redator já iniciou o processo ou o pacote final já foi entregue no seu <strong>Google Drive</strong>, <strong>WhatsApp</strong> e <strong>E-mail</strong>. Não é necessário clicar novamente.
            </p>
        </div>
    </body>
    </html>
    """


def _render_conflict_html(
    escaped_selected_title: str,
    escaped_selected_cat: str,
    escaped_new_title: str,
    escaped_new_cat: str,
    token: str,
) -> str:
    return f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Apenas 1 Tema por Dia</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 16px; }}
            .card {{ background-color: #1e293b; border: 1px solid #eab308; border-radius: 12px; padding: 32px 28px; max-width: 540px; text-align: center; box-shadow: 0 20px 35px rgba(0,0,0,0.4); }}
            .badge {{ display: inline-block; background-color: rgba(234, 179, 8, 0.2); color: #facc15; font-size: 11px; font-weight: 700; letter-spacing: 0.8px; padding: 4px 10px; border-radius: 20px; text-transform: uppercase; margin-bottom: 12px; }}
            h1 {{ font-size: 21px; font-weight: 800; margin: 0 0 12px 0; color: #ffffff; }}
            .box {{ background-color: rgba(15, 23, 42, 0.6); border: 1px solid #334155; border-radius: 8px; padding: 14px 16px; margin: 16px 0; text-align: left; }}
            .label {{ font-size: 11px; text-transform: uppercase; font-weight: 700; color: #94a3b8; margin-bottom: 4px; }}
            .title {{ font-size: 14px; font-weight: 600; color: #f8fafc; line-height: 1.4; }}
            .btn {{ display: inline-block; background-color: #ba2649; color: #ffffff; font-weight: 700; font-size: 13px; padding: 12px 22px; border-radius: 6px; text-decoration: none; margin-top: 18px; transition: background 0.2s; }}
            .btn:hover {{ background-color: #922824; }}
            p.info {{ color: #94a3b8; font-size: 13px; line-height: 1.6; margin: 0; }}
        </style>
    </head>
    <body>
        <div class="card">
            <span class="badge">⚠️ Limite Diário: 1 Pauta por Dia</span>
            <h1>Outro Tema Já Foi Selecionado Hoje</h1>
            
            <p class="info">
                Para manter a profundidade técnica e autoridade do portal <strong>fernandonogueira.dev.br</strong>, selecionamos apenas <strong>1 único tema por dia</strong>.
            </p>

            <div class="box">
                <div class="label">Tema já selecionado hoje:</div>
                <div class="title" style="color: #4ade80;">{escaped_selected_title} ({escaped_selected_cat})</div>
            </div>

            <div class="box">
                <div class="label">Você acabou de clicar em:</div>
                <div class="title" style="color: #38bdf8;">{escaped_new_title} ({escaped_new_cat})</div>
            </div>

            <p class="info">
                Se você clicou por engano e deseja <strong>substituir</strong> o tema anterior por este novo, confirme no botão abaixo:
            </p>

            <a href="/api/v1/editorial/select?token={token}&force=true" class="btn">
                🔄 Substituir e Gerar Este Novo Tema &rarr;
            </a>
        </div>
    </body>
    </html>
    """


def _render_success_html(escaped_title: str, escaped_cat: str, task_id: str) -> str:
    return f"""
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
                <div style="color: #64748b; font-size: 12px; margin-bottom: 8px;">Categoria: {escaped_cat}</div>
                <div class="status-line">
                    <span class="pulse-dot"></span>
                    <span>Agente redator em execução (Task: {task_id[:8]}...)</span>
                </div>
            </div>

            <p class="info">
                O Gemini IA está aprofundando o assunto com buscas na web e redigindo os 4 canais:<br>
                <strong>Blog WordPress</strong> (SEO completo), <strong>LinkedIn Pulse & Feed</strong>, <strong>Instagram</strong> e <strong>Prompts de Imagem</strong>.<br><br>
                Assim que estiver concluído (~60-90 segundos), o documento do <strong>Google Docs</strong> será salvo e o link enviado no seu WhatsApp e E-mail.
            </p>
        </div>
    </body>
    </html>
    """


@router.get("/select", response_class=HTMLResponse)
async def select_editorial_topic(
    token: str = Query(..., description="Signed HMAC token from the daily email button"),
    force: bool = Query(False, description="Forçar substituição caso outro tema já tenha sido escolhido"),
):
    """
    One-click callback endpoint triggered when clicking 'Gerar Conteúdo' in the daily email.
    Validates token and enqueues deep research & writing pipeline in Celery.
    Enforces that only 1 topic per daily curation can be generated unless explicitly forced.
    """
    payload = verify_editorial_action_token(token)
    if not payload:
        logger.warning("Invalid or expired editorial action token presented.")
        return HTMLResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_render_invalid_token_html(),
        )

    pauta_titulo = payload.get("pauta_titulo", "Tema Selecionado")
    categoria = payload.get("categoria", "Tecnologia da Informação")
    curation_date = payload.get("curation_date") or datetime.now().strftime("%Y%m%d")

    escaped_title = html.escape(pauta_titulo)
    escaped_cat = html.escape(categoria)

    # Idempotency lock per daily curation batch
    redis_client = _get_redis()
    redis_key = f"editorial:curation_selected:{curation_date}"

    if redis_client and not force:
        try:
            stored_raw = redis_client.get(redis_key)
            if stored_raw:
                stored = json.loads(stored_raw)
                stored_title = stored.get("pauta_titulo", "")
                stored_cat = stored.get("categoria", "")

                # Case 1: Re-click on the exact same topic
                if stored_title == pauta_titulo or stored.get("pauta_id") == payload.get("pauta_id"):
                    return HTMLResponse(
                        content=_render_already_in_progress_html(escaped_title, escaped_cat)
                    )

                # Case 2: Attempting to select a second, different topic on the same day
                return HTMLResponse(
                    content=_render_conflict_html(
                        escaped_selected_title=html.escape(stored_title),
                        escaped_selected_cat=html.escape(stored_cat),
                        escaped_new_title=escaped_title,
                        escaped_new_cat=escaped_cat,
                        token=token,
                    )
                )
        except Exception as err_check:
            logger.warning("Error reading editorial lock from Redis: %s", err_check)

    # Save selection lock in Redis (3 days TTL)
    if redis_client:
        try:
            redis_client.set(
                redis_key,
                json.dumps({
                    "pauta_id": payload.get("pauta_id"),
                    "pauta_titulo": pauta_titulo,
                    "categoria": categoria,
                    "selected_at": datetime.now().isoformat(),
                }),
                ex=86400 * 3,
            )
        except Exception as err_set:
            logger.warning("Error setting editorial lock in Redis: %s", err_set)

    logger.info("Editorial topic selected: '%s' [%s]. Queuing Celery pipeline...", pauta_titulo, categoria)
    async_task = task_deep_content_generation.delay(payload)

    return HTMLResponse(
        content=_render_success_html(escaped_title, escaped_cat, async_task.id)
    )
