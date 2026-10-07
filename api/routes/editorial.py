"""
FastAPI Routes for Human-in-the-Loop Editorial Topic Selection and Content Triggering.
Receives one-click callback from daily curation email and initiates deep research & drafting pipeline.
Enforces single-topic selection per daily curation batch with conflict detection and idempotency locks.
Features premium responsive confirmation UI matching the daily curation email design language.
"""

from datetime import datetime
import html
import json
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from redis import Redis

from core.config import settings
from core.security import (
    verify_editorial_action_token,
    verify_editorial_publish_token,
    verify_linkedin_comment_action_token,
)
from flows.flow_content_deep_writer import task_deep_content_generation
from flows.flow_content_publisher import task_publish_approved_editorial
from storage.repository import repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/editorial", tags=["Editorial & Content Ops"])

DRIVE_FOLDER_URL = "https://drive.google.com/drive/folders/10hlkHJNWdfAAHU6dicFKW_gaObeG2vRw"


def _get_redis() -> Optional[Redis]:
    """Returns connected Redis client or None if unreachable."""
    try:
        r = Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        r.ping()
        return r
    except Exception as e:
        logger.warning("Could not connect to Redis for editorial idempotency lock: %s", e)
        return None


def _get_category_theme(categoria: str) -> Dict[str, str]:
    """Returns color tokens tailored to category matching the curation email."""
    if "Música" in categoria or "Musica" in categoria:
        return {
            "top_bar": "#be185d",
            "badge_bg": "#fce7f3",
            "badge_text": "#be185d",
            "tag_text": "#fbcfe8",
            "accent_btn": "#be185d",
            "accent_btn_hover": "#9d174d",
            "icon": "🎵",
            "label": "Música & Mercado Musical",
        }
    return {
        "top_bar": "#4f46e5",
        "badge_bg": "#e0e7ff",
        "badge_text": "#4338ca",
        "tag_text": "#93c5fd",
        "accent_btn": "#4f46e5",
        "accent_btn_hover": "#4338ca",
        "icon": "💻",
        "label": "Tecnologia da Informação (TI)",
    }


def _render_page_wrapper(top_bar_color: str, tag_text: str, tag_color: str, title: str, subtitle: str, body_content: str) -> str:
    """Generates the full HTML shell mirroring the daily email visual system."""
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(title)} - fernandonogueira.dev.br</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        * {{
            box-sizing: border-box;
        }}
        body {{
            margin: 0;
            padding: 40px 16px;
            background-color: #f1f5f9;
            font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            color: #0f172a;
            display: flex;
            justify-content: center;
            align-items: flex-start;
            min-height: 100vh;
            -webkit-font-smoothing: antialiased;
        }}
        .container {{
            width: 100%;
            max-width: 620px;
            margin: 0 auto;
        }}
        /* Top Dark Banner matching email header */
        .header-banner {{
            background-color: #0f172a;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 15px rgba(15, 23, 42, 0.08);
            margin-bottom: 24px;
        }}
        .header-stripe {{
            height: 4px;
            background-color: {top_bar_color};
            width: 100%;
        }}
        .header-inner {{
            padding: 26px 28px;
        }}
        .header-tag {{
            display: inline-block;
            background-color: rgba(255, 255, 255, 0.1);
            color: {tag_color};
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 1px;
            padding: 4px 10px;
            border-radius: 4px;
            text-transform: uppercase;
            margin-bottom: 10px;
        }}
        .header-title {{
            margin: 0 0 6px 0;
            color: #ffffff;
            font-size: 22px;
            font-weight: 800;
            line-height: 28px;
            letter-spacing: -0.4px;
        }}
        .header-sub {{
            margin: 0;
            color: #94a3b8;
            font-size: 14px;
            line-height: 20px;
        }}
        /* Main Content Card matching email cards */
        .card {{
            background-color: #ffffff;
            border-radius: 10px;
            border: 1px solid #e2e8f0;
            box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04);
            padding: 26px 28px;
            margin-bottom: 20px;
        }}
        .badge {{
            display: inline-block;
            font-size: 11px;
            font-weight: 800;
            letter-spacing: 0.8px;
            padding: 4px 10px;
            border-radius: 20px;
            text-transform: uppercase;
            margin-bottom: 12px;
        }}
        .topic-title {{
            margin: 0 0 14px 0;
            color: #0f172a;
            font-size: 18px;
            line-height: 25px;
            font-weight: 700;
            letter-spacing: -0.3px;
        }}
        .callout {{
            border-left: 3px solid #cbd5e1;
            padding: 12px 14px;
            background-color: #f8fafc;
            border-radius: 0 6px 6px 0;
            color: #475569;
            font-size: 13px;
            line-height: 20px;
            margin: 16px 0;
        }}
        /* Pipeline Flow Steps */
        .stepper {{
            margin: 24px 0;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}
        .step-item {{
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 10px 14px;
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            font-size: 13px;
            color: #334155;
            font-weight: 500;
        }}
        .step-icon {{
            width: 28px;
            height: 28px;
            border-radius: 50%;
            background-color: #ffffff;
            border: 1px solid #cbd5e1;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 13px;
            flex-shrink: 0;
        }}
        .step-active {{
            border-color: #818cf8;
            background-color: #eef2ff;
            color: #3730a3;
            font-weight: 600;
        }}
        .step-active .step-icon {{
            background-color: #4f46e5;
            color: #ffffff;
            border-color: #4f46e5;
        }}
        .pulse-dot {{
            width: 8px;
            height: 8px;
            background-color: #22c55e;
            border-radius: 50%;
            display: inline-block;
            margin-right: 6px;
            box-shadow: 0 0 8px #22c55e;
            animation: pulse-ring 1.5s infinite;
        }}
        @keyframes pulse-ring {{
            0% {{ opacity: 0.3; transform: scale(0.9); }}
            50% {{ opacity: 1; transform: scale(1.1); }}
            100% {{ opacity: 0.3; transform: scale(0.9); }}
        }}
        /* Action Buttons */
        .btn {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            background-color: #4f46e5;
            color: #ffffff !important;
            padding: 12px 22px;
            border-radius: 6px;
            text-decoration: none;
            font-size: 13px;
            font-weight: 700;
            letter-spacing: 0.3px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.08);
            transition: all 0.15s ease;
            cursor: pointer;
            border: none;
        }}
        .btn:hover {{
            opacity: 0.95;
            transform: translateY(-1px);
            box-shadow: 0 4px 10px rgba(0,0,0,0.12);
        }}
        .btn-outline {{
            background-color: #ffffff;
            color: #334155 !important;
            border: 1px solid #cbd5e1;
            box-shadow: none;
        }}
        .btn-outline:hover {{
            background-color: #f8fafc;
            border-color: #94a3b8;
        }}
        .btn-danger {{
            background-color: #ba2649;
        }}
        .btn-danger:hover {{
            background-color: #922824;
        }}
        /* Footer */
        .footer {{
            text-align: center;
            padding: 14px 8px;
            color: #94a3b8;
            font-size: 12px;
            line-height: 18px;
        }}
        .footer strong {{
            color: #64748b;
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Top Dark Banner -->
        <div class="header-banner">
            <div class="header-stripe"></div>
            <div class="header-inner">
                <span class="header-tag">{tag_text}</span>
                <h1 class="header-title">{html.escape(title)}</h1>
                <p class="header-sub">{html.escape(subtitle)}</p>
            </div>
        </div>

        <!-- Main Content Card -->
        <div class="card">
            {body_content}
        </div>

        <!-- Footer -->
        <div class="footer">
            Gerado automaticamente via <strong>Omni-Flow Python Engine</strong> • <a href="https://www.fernandonogueira.dev.br" target="_blank" style="color: #64748b; text-decoration: none;">fernandonogueira.dev.br</a>
        </div>
    </div>
</body>
</html>
"""


def _render_invalid_token_html() -> str:
    body = """
    <div style="text-align: center; padding: 12px 0;">
        <span class="badge" style="background-color: #ffe4e6; color: #be123c;">⚠️ Erro de Autenticação</span>
        <h2 style="margin: 6px 0 12px 0; color: #0f172a; font-size: 20px; font-weight: 800;">Link Expirado ou Inválido</h2>
        
        <p style="color: #64748b; font-size: 14px; line-height: 22px; max-width: 480px; margin: 0 auto 20px auto;">
            Este link de aprovação expirou (validade máxima de 48h) ou a assinatura digital de segurança HMAC não pôde ser confirmada.
        </p>

        <div class="callout" style="text-align: left;">
            <strong style="color: #334155;">Como proceder:</strong> Acesse seu e-mail e abra a edição mais recente da <em>Curadoria de Conteúdo Diária</em> para selecionar um tema ativo.
        </div>
    </div>
    """
    return _render_page_wrapper(
        top_bar_color="#f43f5e",
        tag_text="✦ Verificação de Segurança",
        tag_color="#fca5a5",
        title="Link Não Reconhecido",
        subtitle="O token criptográfico apresentado não pôde ser validado com segurança.",
        body_content=body,
    )


def _render_already_in_progress_html(escaped_title: str, escaped_cat: str) -> str:
    theme = _get_category_theme(escaped_cat)
    body = f"""
    <div>
        <span class="badge" style="background-color: #dcfce7; color: #15803d;">
            ✅ Tema Já em Produção
        </span>

        <h2 class="topic-title" style="font-size: 19px; margin-top: 4px;">
            {escaped_title}
        </h2>

        <div class="callout">
            <strong style="color: #15803d;">Status da Solicitação:</strong> Você já acionou a produção deste tema hoje.
            O agente redator autônomo já iniciou o ciclo de pesquisa web ou o pacote multicanal já foi compilado.
        </div>

        <p style="color: #475569; font-size: 13px; line-height: 20px; margin: 16px 0;">
            Não é necessário clicar novamente. Os arquivos gerados são organizados diretamente na sua pasta <strong>Editoriais</strong> do Google Drive e os alertas são despachados para o seu WhatsApp e E-mail.
        </p>

        <div style="display: flex; gap: 12px; margin-top: 22px; flex-wrap: wrap;">
            <a href="{DRIVE_FOLDER_URL}" target="_blank" class="btn" style="background-color: {theme['accent_btn']};">
                📁 Acessar Pasta "Editoriais" no Drive &rarr;
            </a>
            <a href="https://mail.google.com" target="_blank" class="btn btn-outline">
                📧 Abrir Gmail
            </a>
        </div>
    </div>
    """
    return _render_page_wrapper(
        top_bar_color=theme["top_bar"],
        tag_text="✦ Confirmação Editorial",
        tag_color=theme["tag_text"],
        title="Pauta Já Selecionada",
        subtitle="A redação deste conteúdo já foi encomendada e está registrada no pipeline.",
        body_content=body,
    )


def _render_conflict_html(
    escaped_selected_title: str,
    escaped_selected_cat: str,
    escaped_new_title: str,
    escaped_new_cat: str,
    token: str,
) -> str:
    theme_new = _get_category_theme(escaped_new_cat)
    theme_old = _get_category_theme(escaped_selected_cat)
    body = f"""
    <div>
        <span class="badge" style="background-color: #fef9c3; color: #a16207;">
            ⚠️ Regra Editorial • 1 Tema por Dia
        </span>

        <h2 style="margin: 4px 0 10px 0; color: #0f172a; font-size: 19px; font-weight: 800;">
            Outro Tema Já Foi Escolhido Hoje
        </h2>

        <p style="color: #475569; font-size: 13px; line-height: 21px; margin: 0 0 16px 0;">
            Para manter a autoridade técnica e máxima densidade factual de <strong>fernandonogueira.dev.br</strong>, produzimos com profundidade <strong>apenas 1 tema por dia</strong>.
        </p>

        <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px 16px; margin-bottom: 12px;">
            <div style="font-size: 11px; font-weight: 800; color: #15803d; text-transform: uppercase; margin-bottom: 4px;">
                ✓ Tema já selecionado hoje ({theme_old['icon']} {escaped_selected_cat}):
            </div>
            <div style="font-size: 14px; font-weight: 700; color: #0f172a; line-height: 20px;">
                {escaped_selected_title}
            </div>
        </div>

        <div style="background-color: #fffbeb; border: 1px solid #fde68a; border-radius: 8px; padding: 14px 16px; margin-bottom: 20px;">
            <div style="font-size: 11px; font-weight: 800; color: #b45309; text-transform: uppercase; margin-bottom: 4px;">
                ➜ Novo tema em que você clicou ({theme_new['icon']} {escaped_new_cat}):
            </div>
            <div style="font-size: 14px; font-weight: 700; color: #92400e; line-height: 20px;">
                {escaped_new_title}
            </div>
        </div>

        <div class="callout">
            <strong style="color: #334155;">Deseja trocar de tema?</strong> Se você clicou por engano ou mudou de ideia e quer produzir este novo assunto, clique em substituir abaixo:
        </div>

        <div style="display: flex; gap: 12px; margin-top: 20px; flex-wrap: wrap;">
            <a href="/api/v1/editorial/select?token={token}&force=true" class="btn btn-danger" style="background-color: {theme_new['accent_btn']};">
                🔄 Sim, Substituir e Gerar Novo Tema &rarr;
            </a>
            <a href="{DRIVE_FOLDER_URL}" target="_blank" class="btn btn-outline">
                📁 Manter o Tema Atual e Ver Drive
            </a>
        </div>
    </div>
    """
    return _render_page_wrapper(
        top_bar_color="#f59e0b",
        tag_text="✦ Conflito de Seleção",
        tag_color="#fde68a",
        title="Apenas 1 Artigo Diário",
        subtitle="Um tema já havia sido despachado para produção hoje. Você pode confirmar a troca se preferir.",
        body_content=body,
    )


def _render_success_html(escaped_title: str, escaped_cat: str, task_id: str) -> str:
    theme = _get_category_theme(escaped_cat)
    body = f"""
    <div>
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; flex-wrap: wrap; gap: 8px;">
            <span class="badge" style="background-color: {theme['badge_bg']}; color: {theme['badge_text']}; margin-bottom: 0;">
                {theme['icon']} {escaped_cat}
            </span>
            <span style="font-size: 12px; color: #16a34a; font-weight: 700; display: inline-flex; align-items: center;">
                <span class="pulse-dot"></span> Pipeline Ativo (ID: {task_id[:8]})
            </span>
        </div>

        <h2 class="topic-title">
            {escaped_title}
        </h2>

        <div class="callout">
            <strong style="color: #0f172a;">Tema confirmado com sucesso!</strong> O agente autônomo está realizando a investigação web na internet e produzindo o pacote editorial completo para <strong>fernandonogueira.dev.br</strong>.
        </div>

        <!-- Etapas em Andamento -->
        <div class="stepper">
            <div class="step-item step-active">
                <div class="step-icon">🔍</div>
                <div style="flex: 1;">
                    <div style="font-weight: 700;">1. Agente de Pesquisa Autônomo</div>
                    <div style="font-size: 11px; opacity: 0.85;">Buscas no Google, scraping com Chromium stealth e digestão factual</div>
                </div>
                <span class="pulse-dot" style="margin-left: auto;"></span>
            </div>

            <div class="step-item">
                <div class="step-icon">✍️</div>
                <div>
                    <div style="font-weight: 700; color: #475569;">2. Redação Multicanal (Gemini AI)</div>
                    <div style="font-size: 11px; color: #94a3b8;">Blog (SEO e casos reais), LinkedIn Pulse & Feed, Reels Instagram e Prompts de Imagem</div>
                </div>
            </div>

            <div class="step-item">
                <div class="step-icon">📁</div>
                <div>
                    <div style="font-weight: 700; color: #475569;">3. Organização no Google Drive</div>
                    <div style="font-size: 11px; color: #94a3b8;">Criação do Google Doc formatado e salvamento na pasta <em>Editoriais</em></div>
                </div>
            </div>

            <div class="step-item">
                <div class="step-icon">📲</div>
                <div>
                    <div style="font-weight: 700; color: #475569;">4. Notificação Instantânea</div>
                    <div style="font-size: 11px; color: #94a3b8;">Aviso com link direto via WhatsApp (Evolution API) e E-mail (Gmail)</div>
                </div>
            </div>
        </div>

        <div style="background-color: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 8px; padding: 14px 16px; margin: 20px 0;">
            <div style="font-size: 12px; color: #475569; line-height: 19px;">
                ⏱️ <strong>Tempo estimado:</strong> ~60 a 90 segundos.<br>
                Você já pode fechar esta aba com tranquilidade. Assim que o documento for gerado, você receberá a notificação com o link direto no seu celular.
            </div>
        </div>

        <div style="display: flex; gap: 12px; margin-top: 20px; flex-wrap: wrap;">
            <a href="{DRIVE_FOLDER_URL}" target="_blank" class="btn" style="background-color: {theme['accent_btn']};">
                📁 Acessar Pasta "Editoriais" no Drive &rarr;
            </a>
            <a href="https://web.whatsapp.com" target="_blank" class="btn btn-outline">
                💬 Abrir WhatsApp Web
            </a>
        </div>
    </div>
    """
    return _render_page_wrapper(
        top_bar_color=theme["top_bar"],
        tag_text="✦ Confirmação de Pauta",
        tag_color=theme["tag_text"],
        title="Tema Selecionado com Sucesso!",
        subtitle=f"Produção iniciada para a categoria {escaped_cat}.",
        body_content=body,
    )


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

    # Sync pool status to EM_PRODUCAO
    try:
        pool_matches = repo.list_editorial_pautas_pool(search=pauta_titulo, limit=5)
        for m in pool_matches:
            if m["titulo"].strip().lower() == pauta_titulo.strip().lower():
                repo.update_editorial_pauta_status(m["id"], "EM_PRODUCAO")
                break
    except Exception as e_pool:
        logger.debug("Could not update pool status from select: %s", e_pool)

    return HTMLResponse(
        content=_render_success_html(escaped_title, escaped_cat, async_task.id)
    )


def _render_publish_success_html(escaped_title: str, escaped_cat: str, task_id: str, doc_url: str) -> str:
    theme = _get_category_theme(escaped_cat)
    body = f"""
    <div>
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; flex-wrap: wrap; gap: 8px;">
            <span class="badge" style="background-color: {theme['badge_bg']}; color: {theme['badge_text']}; margin-bottom: 0;">
                {theme['icon']} {escaped_cat}
            </span>
            <span style="font-size: 12px; color: #16a34a; font-weight: 700; display: inline-flex; align-items: center;">
                <span class="pulse-dot"></span> Publicação em Andamento (ID: {task_id[:8]})
            </span>
        </div>

        <h2 class="topic-title">
            {escaped_title}
        </h2>

        <div class="callout">
            <strong style="color: #0f172a;">Aprovação confirmada com sucesso!</strong>
            O robô está extraindo o texto com as suas revisões do Google Docs e publicando automaticamente no <strong>Blog</strong> e no <strong>LinkedIn</strong>.
        </div>

        <!-- Etapas em Andamento -->
        <div class="stepper">
            <div class="step-item step-active">
                <div class="step-icon">📄</div>
                <div style="flex: 1;">
                    <div style="font-weight: 700;">1. Extração do Google Docs</div>
                    <div style="font-size: 11px; opacity: 0.85;">Baixando texto revisado pelo autor com fidelidade estrita</div>
                </div>
                <span class="pulse-dot" style="margin-left: auto;"></span>
            </div>

            <div class="step-item">
                <div class="step-icon">🌐</div>
                <div>
                    <div style="font-weight: 700; color: #475569;">2. Publicação no Blog Oficial</div>
                    <div style="font-size: 11px; color: #94a3b8;">Inserção direta no banco PostgreSQL (fernandonogueira.dev.br/blog)</div>
                </div>
            </div>

            <div class="step-item">
                <div class="step-icon">💼</div>
                <div>
                    <div style="font-weight: 700; color: #475569;">3. Postagem no LinkedIn</div>
                    <div style="font-size: 11px; color: #94a3b8;">Chromium autenticado com sessão salva postando no Feed e Pulse</div>
                </div>
            </div>

            <div class="step-item">
                <div class="step-icon">📲</div>
                <div>
                    <div style="font-weight: 700; color: #475569;">4. Notificação de Conclusão</div>
                    <div style="font-size: 11px; color: #94a3b8;">Aviso imediato no seu WhatsApp com os links no ar</div>
                </div>
            </div>
        </div>

        <div style="background-color: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 8px; padding: 14px 16px; margin: 20px 0;">
            <div style="font-size: 12px; color: #475569; line-height: 19px;">
                ⏱️ <strong>Tempo estimado:</strong> ~20 a 40 segundos.<br>
                Assim que a publicação for concluída, você receberá a notificação com os links diretos no seu celular.
            </div>
        </div>

        <div style="display: flex; gap: 12px; margin-top: 20px; flex-wrap: wrap;">
            <a href="https://www.fernandonogueira.dev.br/blog" target="_blank" class="btn" style="background-color: {theme['accent_btn']};">
                🌐 Ver Blog no Ar &rarr;
            </a>
            <a href="https://www.linkedin.com/feed/" target="_blank" class="btn btn-outline">
                💼 Abrir LinkedIn
            </a>
            <a href="{doc_url}" target="_blank" class="btn btn-outline">
                📄 Ver Google Doc
            </a>
        </div>
    </div>
    """
    return _render_page_wrapper(
        top_bar_color="#16a34a",
        tag_text="✦ Publicação Autorizada",
        tag_color="#86efac",
        title="Publicação Iniciada!",
        subtitle=f"Despachando conteúdo aprovado para {escaped_cat}.",
        body_content=body,
    )


@router.get("/publish", response_class=HTMLResponse)
async def publish_approved_editorial_endpoint(
    token: str = Query(..., description="Signed HMAC token from the WhatsApp or email publish button"),
):
    """
    One-click callback endpoint triggered when clicking 'Aprovar e Publicar' after reviewing Google Docs.
    Validates HMAC token, applies idempotency lock, and enqueues Phase 1 publication (Site + LinkedIn).
    """
    payload = verify_editorial_publish_token(token)
    if not payload:
        logger.warning("Invalid or expired editorial publish token presented.")
        return HTMLResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_render_invalid_token_html(),
        )

    doc_id = payload.get("doc_id")
    pauta_titulo = payload.get("pauta_titulo", "Artigo Editorial")
    categoria = payload.get("categoria", "Tecnologia da Informação")
    doc_url = payload.get("doc_url") or f"https://docs.google.com/document/d/{doc_id}/edit"

    escaped_title = html.escape(pauta_titulo)
    escaped_cat = html.escape(categoria)

    # Idempotency lock per doc_id publication
    redis_client = _get_redis()
    redis_key = f"editorial:publish_locked:{doc_id}"

    if redis_client:
        try:
            already_publishing = redis_client.get(redis_key)
            if already_publishing:
                logger.info("Publish already in progress for doc_id %s", doc_id)
                theme = _get_category_theme(escaped_cat)
                body = f"""
                <div>
                    <span class="badge" style="background-color: #dcfce7; color: #15803d;">
                        ✓ Publicação Já em Execução
                    </span>
                    <h2 class="topic-title" style="margin-top: 6px;">{escaped_title}</h2>
                    <p style="color: #475569; font-size: 13px; line-height: 20px;">
                        A publicação deste artigo já foi solicitada e está sendo processada pelos robôs do Blog e do LinkedIn.
                        Assim que terminar, os links serão enviados ao seu WhatsApp.
                    </p>
                    <div style="display: flex; gap: 12px; margin-top: 20px;">
                        <a href="https://www.fernandonogueira.dev.br/blog" target="_blank" class="btn" style="background-color: {theme['accent_btn']};">
                            🌐 Acessar Blog
                        </a>
                        <a href="{doc_url}" target="_blank" class="btn btn-outline">
                            📄 Ver Google Doc
                        </a>
                    </div>
                </div>
                """
                return HTMLResponse(
                    content=_render_page_wrapper(
                        top_bar_color=theme["top_bar"],
                        tag_text="✦ Status da Publicação",
                        tag_color=theme["tag_text"],
                        title="Processamento em Andamento",
                        subtitle="A ordem de publicação já havia sido recebida.",
                        body_content=body,
                    )
                )

            redis_client.set(redis_key, "1", ex=86400 * 2)
        except Exception as err_lock:
            logger.warning("Error with publish Redis lock: %s", err_lock)

    logger.info("Editorial publish authorized for doc_id %s ('%s'). Queuing Celery pipeline...", doc_id, pauta_titulo)
    async_task = task_publish_approved_editorial.delay(payload)

    return HTMLResponse(
        content=_render_publish_success_html(escaped_title, escaped_cat, async_task.id, doc_url)
    )


# ==============================================================================
# Central Editorial Web Hub (/editorial/hub)
# ==============================================================================

def _render_hub_page(
    stats: Dict[str, Any],
    pautas: List[Dict[str, Any]],
    active_tab: str = "backlog",
    active_cat: str = "all",
    search_query: str = "",
    msg: str = "",
) -> str:
    """
    Renders the modern, responsive Editorial Hub interface for backlog browsing,
    topic suggestions, and publication monitoring.
    """
    disponiveis = stats.get("disponiveis", 0)
    publicados = stats.get("publicados", 0)
    arquivados = stats.get("arquivados", 0)
    ti_disp = stats.get("ti_disponiveis", 0)
    musica_disp = stats.get("musica_disponiveis", 0)
    manuais = stats.get("manuais", 0)

    # Toast / Alert messages
    alert_html = ""
    if msg == "suggest_saved":
        alert_html = """
        <div class="hub-alert alert-success">
            <span>✓</span> <strong>Novo tema registrado com sucesso!</strong> A pauta já está disponível no seu backlog.
        </div>
        """
    elif msg == "archived":
        alert_html = """
        <div class="hub-alert alert-info">
            <span>📦</span> <strong>Pauta arquivada.</strong> Você pode acessá-la ou restaurá-la na aba 'Arquivadas'.
        </div>
        """
    elif msg == "restored":
        alert_html = """
        <div class="hub-alert alert-success">
            <span>♻️</span> <strong>Pauta restaurada!</strong> Ela voltou a ficar ativa no seu Banco de Pautas.
        </div>
        """
    elif msg == "error_empty":
        alert_html = """
        <div class="hub-alert alert-error">
            <span>⚠️</span> O título do tema é obrigatório para cadastrar uma nova sugestão.
        </div>
        """

    # Tab content generation
    tab_content = ""
    if active_tab == "suggest":
        tab_content = f"""
        <div class="hub-card form-card">
            <div class="form-header">
                <span class="badge" style="background-color: #fef3c7; color: #b45309;">💡 Sugestão Manual</span>
                <h2 style="margin: 10px 0 6px 0; font-size: 20px; color: #0f172a;">Propor Novo Tema Editorial</h2>
                <p style="margin: 0; color: #64748b; font-size: 13px;">
                    Insira uma ideia própria de tema para guardar no backlog ou disparar na hora a pesquisa profunda com Playwright e redação multicanal.
                </p>
            </div>
            
            <form action="/api/v1/editorial/hub/suggest" method="post" style="margin-top: 24px;">
                <div class="form-group">
                    <label for="titulo" class="form-label">Título da Pauta / Assunto Central <span style="color: #ef4444;">*</span></label>
                    <input type="text" id="titulo" name="titulo" class="form-control" required 
                           placeholder="Ex: Como a Nova Diretriz de Cibersegurança Altera o Uso de Agentes de IA nas Finanças" />
                </div>

                <div class="form-group">
                    <label for="categoria" class="form-label">Categoria Editorial</label>
                    <select id="categoria" name="categoria" class="form-control">
                        <option value="Tecnologia da Informação (TI)">💻 Tecnologia da Informação (TI)</option>
                        <option value="Música & Mercado Musical">🎵 Música & Mercado Musical</option>
                    </select>
                </div>

                <div class="form-group">
                    <label for="angulo_editorial" class="form-label">Ângulo Editorial / Tese Central (Opcional)</label>
                    <textarea id="angulo_editorial" name="angulo_editorial" class="form-control" rows="3"
                              placeholder="Qual é o ponto de vista crítico, lição de liderança ou reflexão técnica que o artigo deve desenvolver?"></textarea>
                </div>

                <div class="form-group">
                    <label for="fontes" class="form-label">Links de Apoio ou Matérias Base (Opcional, 1 por linha)</label>
                    <textarea id="fontes" name="fontes" class="form-control" rows="2"
                              placeholder="https://exemplo.com/noticia-sobre-o-tema&#10;https://outro.org/relatorio.pdf"></textarea>
                </div>

                <div class="form-actions">
                    <button type="submit" name="action_mode" value="save" class="btn btn-secondary">
                        💾 Guardar no Banco de Pautas
                    </button>
                    <button type="submit" name="action_mode" value="produce" class="btn btn-primary" onclick="return confirm('Deseja iniciar a produção autônoma imediata deste artigo (pesquisa profunda no Google + Google Docs)?');">
                        ⚡ Produzir Imediatamente (1 Clique)
                    </button>
                </div>
            </form>
        </div>
        """
    elif active_tab == "published":
        if not pautas:
            tab_content = """
            <div class="empty-state">
                <div class="empty-icon">📰</div>
                <h3>Nenhum artigo publicado encontrado</h3>
                <p>Assim que os artigos forem aprovados e veiculados no Blog, eles aparecerão listados aqui.</p>
            </div>
            """
        else:
            cards_html = []
            for p in pautas:
                escaped_title = html.escape(p.get("titulo", "Artigo"))
                escaped_cat = html.escape(p.get("categoria", "Geral"))
                created_date = p.get("created_at", "")[:10]
                theme = _get_category_theme(escaped_cat)
                
                cards_html.append(f"""
                <div class="pauta-card">
                    <div class="pauta-header">
                        <span class="badge" style="background-color: {theme['badge_bg']}; color: {theme['badge_text']};">
                            {theme['icon']} {escaped_cat}
                        </span>
                        <span class="badge" style="background-color: #dcfce7; color: #15803d;">✓ Publicado</span>
                        <span class="pauta-date">{created_date}</span>
                    </div>
                    <h3 class="pauta-title">{escaped_title}</h3>
                    {f'<div class="pauta-angle"><strong>Ângulo:</strong> {html.escape(p["angulo_editorial"])}</div>' if p.get("angulo_editorial") else ''}
                    <div style="display: flex; gap: 10px; margin-top: 14px; flex-wrap: wrap;">
                        <a href="https://www.fernandonogueira.dev.br/blog" target="_blank" class="btn btn-sm btn-outline">
                            🌐 Ver no Blog
                        </a>
                    </div>
                </div>
                """)
            tab_content = "".join(cards_html)
    elif active_tab == "archived":
        if not pautas:
            tab_content = """
            <div class="empty-state">
                <div class="empty-icon">📦</div>
                <h3>Nenhuma pauta arquivada</h3>
                <p>Pautas descartadas aparecerão aqui e poderão ser restauradas para o backlog a qualquer momento.</p>
            </div>
            """
        else:
            cards_html = []
            for p in pautas:
                escaped_title = html.escape(p.get("titulo", "Pauta"))
                escaped_cat = html.escape(p.get("categoria", "Geral"))
                created_date = p.get("created_at", "")[:10]
                p_id = p.get("id")
                cards_html.append(f"""
                <div class="pauta-card" style="opacity: 0.85;">
                    <div class="pauta-header">
                        <span class="badge" style="background-color: #f1f5f9; color: #64748b;">
                            {escaped_cat}
                        </span>
                        <span class="badge" style="background-color: #fee2e2; color: #991b1b;">📦 Arquivada</span>
                        <span class="pauta-date">{created_date}</span>
                    </div>
                    <h3 class="pauta-title">{escaped_title}</h3>
                    <div style="margin-top: 14px;">
                        <a href="/api/v1/editorial/hub/archive/{p_id}" class="btn btn-sm btn-secondary">
                            ♻️ Restaurar para Backlog
                        </a>
                    </div>
                </div>
                """)
            tab_content = "".join(cards_html)
    else:  # backlog
        cat_filter_bar = f"""
        <div class="filter-bar">
            <div class="filter-pills">
                <a href="/api/v1/editorial/hub?tab=backlog&cat=all{f'&q={search_query}' if search_query else ''}" 
                   class="pill {'pill-active' if active_cat == 'all' else ''}">
                   Todas ({disponiveis})
                </a>
                <a href="/api/v1/editorial/hub?tab=backlog&cat=ti{f'&q={search_query}' if search_query else ''}" 
                   class="pill {'pill-active' if active_cat == 'ti' else ''}">
                   💻 Tecnologia ({ti_disp})
                </a>
                <a href="/api/v1/editorial/hub?tab=backlog&cat=musica{f'&q={search_query}' if search_query else ''}" 
                   class="pill {'pill-active' if active_cat == 'musica' else ''}">
                   🎵 Música ({musica_disp})
                </a>
            </div>

            <form action="/api/v1/editorial/hub" method="get" class="search-form">
                <input type="hidden" name="tab" value="backlog" />
                <input type="hidden" name="cat" value="{active_cat}" />
                <input type="text" name="q" value="{html.escape(search_query)}" placeholder="Buscar por termo ou tese..." class="search-input" />
                <button type="submit" class="search-btn">🔍</button>
                {f'<a href="/api/v1/editorial/hub?tab=backlog&cat={active_cat}" class="search-clear">✖</a>' if search_query else ''}
            </form>
        </div>
        """

        if not pautas:
            tab_content = cat_filter_bar + f"""
            <div class="empty-state">
                <div class="empty-icon">🔍</div>
                <h3>Nenhuma pauta disponível encontrada</h3>
                <p>Tente alterar o filtro de categoria ou o termo da busca.</p>
                <div style="margin-top: 16px;">
                    <a href="/api/v1/editorial/hub?tab=suggest" class="btn btn-primary">💡 Sugerir Novo Tema</a>
                </div>
            </div>
            """
        else:
            cards_html = [cat_filter_bar]
            for p in pautas:
                escaped_title = html.escape(p.get("titulo", "Pauta"))
                escaped_cat = html.escape(p.get("categoria", "Geral"))
                origem = p.get("origem", "DAILY_CURATION")
                created_date = p.get("created_at", "")[:10]
                p_id = p.get("id")
                theme = _get_category_theme(escaped_cat)

                origem_badge = """<span class="badge" style="background-color: #fef3c7; color: #b45309;">💡 Manual</span>""" if origem == "MANUAL" else """<span class="badge" style="background-color: #f1f5f9; color: #475569;">🤖 Curadoria</span>"""

                angulo_html = f"""<div class="pauta-angle"><strong>Tese / Ângulo:</strong> {html.escape(p['angulo_editorial'])}</div>""" if p.get("angulo_editorial") else ""
                
                sintese_html = f"""<div class="pauta-sintese">{html.escape(p['sintese_factual'][:260])}{'...' if len(p.get('sintese_factual', '')) > 260 else ''}</div>""" if p.get("sintese_factual") else ""

                cards_html.append(f"""
                <div class="pauta-card">
                    <div class="pauta-header">
                        <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap;">
                            <span class="badge" style="background-color: {theme['badge_bg']}; color: {theme['badge_text']};">
                                {theme['icon']} {escaped_cat}
                            </span>
                            {origem_badge}
                        </div>
                        <span class="pauta-date">{created_date}</span>
                    </div>

                    <h3 class="pauta-title">{escaped_title}</h3>
                    {angulo_html}
                    {sintese_html}

                    <div class="pauta-actions">
                        <a href="/api/v1/editorial/hub/produce/{p_id}" class="btn btn-primary btn-sm" 
                           onclick="return confirm('Iniciar produção autônoma deste artigo? O robô fará pesquisa profunda no Google e criará o Google Docs para sua revisão.');">
                            🚀 Produzir Este Artigo
                        </a>
                        <a href="/api/v1/editorial/hub/archive/{p_id}" class="btn btn-secondary btn-sm"
                           onclick="return confirm('Mover esta pauta para o arquivo?');" title="Arquivar pauta">
                            🗑️ Arquivar
                        </a>
                    </div>
                </div>
                """)
            tab_content = "".join(cards_html)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Central Editorial & Banco de Temas - fernandonogueira.dev.br</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            padding: 30px 16px 60px 16px;
            background-color: #f1f5f9;
            font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
            color: #0f172a;
            -webkit-font-smoothing: antialiased;
        }}
        .hub-container {{
            width: 100%;
            max-width: 860px;
            margin: 0 auto;
        }}
        /* Header Hero */
        .hub-hero {{
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            border-radius: 14px;
            overflow: hidden;
            box-shadow: 0 10px 25px -5px rgba(15, 23, 42, 0.15);
            margin-bottom: 24px;
            border-top: 4px solid #3b82f6;
        }}
        .hub-hero-inner {{
            padding: 30px 32px;
        }}
        .hero-tag {{
            display: inline-block;
            background-color: rgba(59, 130, 246, 0.2);
            color: #93c5fd;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 1px;
            padding: 4px 10px;
            border-radius: 4px;
            text-transform: uppercase;
            margin-bottom: 12px;
        }}
        .hero-title {{
            margin: 0 0 8px 0;
            color: #ffffff;
            font-size: 24px;
            font-weight: 800;
            letter-spacing: -0.5px;
        }}
        .hero-subtitle {{
            margin: 0 0 20px 0;
            color: #94a3b8;
            font-size: 14px;
            line-height: 22px;
            max-width: 680px;
        }}
        .hero-stats {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }}
        .stat-pill {{
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.12);
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 12px;
            color: #e2e8f0;
            font-weight: 600;
        }}
        .stat-pill strong {{ color: #ffffff; }}

        /* Tabs Nav */
        .tabs-nav {{
            display: flex;
            background-color: #ffffff;
            border-radius: 10px;
            padding: 6px;
            box-shadow: 0 2px 6px rgba(15, 23, 42, 0.04);
            border: 1px solid #e2e8f0;
            margin-bottom: 20px;
            overflow-x: auto;
            gap: 6px;
        }}
        .tab-btn {{
            flex: 1;
            text-align: center;
            padding: 10px 14px;
            font-size: 13px;
            font-weight: 700;
            color: #64748b;
            text-decoration: none;
            border-radius: 8px;
            transition: all 0.2s ease;
            white-space: nowrap;
        }}
        .tab-btn:hover {{
            background-color: #f8fafc;
            color: #0f172a;
        }}
        .tab-btn.tab-active {{
            background-color: #0f172a;
            color: #ffffff;
            box-shadow: 0 2px 4px rgba(15, 23, 42, 0.1);
        }}

        /* Alerts */
        .hub-alert {{
            padding: 14px 18px;
            border-radius: 8px;
            font-size: 13px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .alert-success {{
            background-color: #dcfce7;
            color: #15803d;
            border: 1px solid #bbf7d0;
        }}
        .alert-info {{
            background-color: #e0f2fe;
            color: #0369a1;
            border: 1px solid #bae6fd;
        }}
        .alert-error {{
            background-color: #fee2e2;
            color: #991b1b;
            border: 1px solid #fecaca;
        }}

        /* Filter & Search Bar */
        .filter-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 20px;
        }}
        .filter-pills {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .pill {{
            padding: 6px 14px;
            border-radius: 20px;
            background-color: #ffffff;
            border: 1px solid #cbd5e1;
            color: #475569;
            font-size: 12px;
            font-weight: 600;
            text-decoration: none;
            transition: all 0.15s ease;
        }}
        .pill:hover {{
            border-color: #94a3b8;
            color: #0f172a;
        }}
        .pill-active {{
            background-color: #3b82f6;
            color: #ffffff !important;
            border-color: #3b82f6;
        }}
        .search-form {{
            display: flex;
            align-items: center;
            background-color: #ffffff;
            border: 1px solid #cbd5e1;
            border-radius: 20px;
            padding: 2px 10px;
        }}
        .search-input {{
            border: none;
            outline: none;
            padding: 6px 8px;
            font-size: 12px;
            color: #0f172a;
            width: 180px;
            background: transparent;
        }}
        .search-btn {{
            background: none;
            border: none;
            cursor: pointer;
            font-size: 14px;
        }}
        .search-clear {{
            color: #94a3b8;
            text-decoration: none;
            font-size: 12px;
            margin-left: 4px;
        }}

        /* Pauta Cards */
        .pauta-card {{
            background-color: #ffffff;
            border-radius: 12px;
            border: 1px solid #e2e8f0;
            padding: 22px 24px;
            margin-bottom: 16px;
            box-shadow: 0 2px 4px rgba(15, 23, 42, 0.03);
            transition: transform 0.15s ease, box-shadow 0.15s ease;
        }}
        .pauta-card:hover {{
            box-shadow: 0 6px 16px rgba(15, 23, 42, 0.07);
        }}
        .pauta-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            flex-wrap: wrap;
            gap: 8px;
        }}
        .badge {{
            display: inline-block;
            font-size: 11px;
            font-weight: 800;
            letter-spacing: 0.6px;
            padding: 3px 10px;
            border-radius: 20px;
            text-transform: uppercase;
        }}
        .pauta-date {{
            font-size: 12px;
            color: #94a3b8;
            font-weight: 500;
        }}
        .pauta-title {{
            margin: 0 0 10px 0;
            font-size: 18px;
            font-weight: 700;
            color: #0f172a;
            line-height: 25px;
            letter-spacing: -0.3px;
        }}
        .pauta-angle {{
            border-left: 3px solid #cbd5e1;
            padding-left: 12px;
            color: #475569;
            font-size: 13px;
            line-height: 20px;
            margin-bottom: 12px;
            font-style: italic;
        }}
        .pauta-sintese {{
            background-color: #f8fafc;
            border-radius: 6px;
            padding: 12px 14px;
            color: #334155;
            font-size: 13px;
            line-height: 20px;
            margin-bottom: 16px;
        }}
        .pauta-actions {{
            display: flex;
            justify-content: flex-end;
            gap: 10px;
            border-top: 1px solid #f1f5f9;
            padding-top: 14px;
            align-items: center;
        }}

        /* Buttons */
        .btn {{
            display: inline-block;
            font-family: inherit;
            font-weight: 700;
            font-size: 13px;
            padding: 9px 18px;
            border-radius: 6px;
            text-decoration: none;
            cursor: pointer;
            border: none;
            transition: all 0.2s ease;
        }}
        .btn-primary {{
            background-color: #0f172a;
            color: #ffffff;
        }}
        .btn-primary:hover {{
            background-color: #334155;
        }}
        .btn-secondary {{
            background-color: #f1f5f9;
            color: #475569;
            border: 1px solid #cbd5e1;
        }}
        .btn-secondary:hover {{
            background-color: #e2e8f0;
            color: #0f172a;
        }}
        .btn-outline {{
            background-color: transparent;
            color: #0f172a;
            border: 1px solid #cbd5e1;
        }}
        .btn-outline:hover {{
            background-color: #f8fafc;
        }}
        .btn-sm {{
            padding: 7px 14px;
            font-size: 12px;
        }}

        /* Form Card */
        .form-card {{
            background-color: #ffffff;
            border-radius: 12px;
            padding: 30px;
            box-shadow: 0 4px 12px rgba(15, 23, 42, 0.05);
            border: 1px solid #e2e8f0;
        }}
        .form-group {{
            margin-bottom: 20px;
        }}
        .form-label {{
            display: block;
            font-size: 13px;
            font-weight: 700;
            color: #334155;
            margin-bottom: 6px;
        }}
        .form-control {{
            width: 100%;
            padding: 10px 14px;
            font-family: inherit;
            font-size: 14px;
            color: #0f172a;
            border: 1px solid #cbd5e1;
            border-radius: 6px;
            outline: none;
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
        }}
        .form-control:focus {{
            border-color: #3b82f6;
            box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15);
        }}
        .form-actions {{
            display: flex;
            justify-content: flex-end;
            gap: 12px;
            margin-top: 28px;
            border-top: 1px solid #f1f5f9;
            padding-top: 20px;
            flex-wrap: wrap;
        }}

        /* Empty State */
        .empty-state {{
            background-color: #ffffff;
            border-radius: 12px;
            padding: 48px 24px;
            text-align: center;
            border: 1px dashed #cbd5e1;
            color: #64748b;
        }}
        .empty-icon {{
            font-size: 40px;
            margin-bottom: 12px;
        }}
        .empty-state h3 {{
            color: #0f172a;
            margin: 0 0 6px 0;
            font-size: 18px;
        }}
        .empty-state p {{
            margin: 0;
            font-size: 13px;
        }}
    </style>
</head>
<body>
    <div class="hub-container">
        <!-- Hero Header -->
        <div class="hub-hero">
            <div class="hub-hero-inner">
                <span class="hero-tag">✦ Central Editorial & Backlog</span>
                <h1 class="hero-title">Gestão e Seleção de Temas</h1>
                <p class="hero-subtitle">
                    Escolha qualquer pauta já minerada que ainda não foi publicada ou sugira uma ideia própria para acionar a pesquisa profunda e redação autônoma.
                </p>
                <div class="hero-stats">
                    <div class="stat-pill"><strong>{disponiveis}</strong> Pautas no Backlog</div>
                    <div class="stat-pill">💻 <strong>{ti_disp}</strong> TI</div>
                    <div class="stat-pill">🎵 <strong>{musica_disp}</strong> Música</div>
                    <div class="stat-pill">💡 <strong>{manuais}</strong> Sugestões Próprias</div>
                    <div class="stat-pill">✓ <strong>{publicados}</strong> Já Publicados</div>
                </div>
            </div>
        </div>

        <!-- Feedback Alert -->
        {alert_html}

        <!-- Tabs Bar -->
        <div class="tabs-nav">
            <a href="/api/v1/editorial/hub?tab=backlog" class="tab-btn {'tab-active' if active_tab == 'backlog' else ''}">
                📚 Banco de Pautas ({disponiveis})
            </a>
            <a href="/api/v1/editorial/hub?tab=suggest" class="tab-btn {'tab-active' if active_tab == 'suggest' else ''}">
                💡 Sugerir Novo Tema
            </a>
            <a href="/api/v1/editorial/hub?tab=published" class="tab-btn {'tab-active' if active_tab == 'published' else ''}">
                ✅ Histórico Publicado ({publicados})
            </a>
            <a href="/api/v1/editorial/hub?tab=archived" class="tab-btn {'tab-active' if active_tab == 'archived' else ''}">
                📦 Arquivadas ({arquivados})
            </a>
        </div>

        <!-- Main Tab Content -->
        {tab_content}
    </div>
</body>
</html>
"""


@router.get("/hub", response_class=HTMLResponse)
async def editorial_hub(
    tab: str = Query("backlog", description="Aba ativa: backlog, suggest, published, archived"),
    cat: str = Query("all", description="Filtro de categoria: all, ti, musica"),
    q: Optional[str] = Query(None, description="Busca textual por palavra-chave"),
    msg: Optional[str] = Query(None, description="Mensagem de feedback"),
):
    """
    Web dashboard for managing the editorial backlog, picking past topics,
    and submitting custom ideas.
    """
    stats = repo.get_editorial_pautas_stats()

    if tab == "published":
        pautas = repo.list_editorial_pautas_pool(
            status="PUBLICADO",
            categoria=cat if cat != "all" else None,
            search=q,
            limit=100,
        )
    elif tab == "archived":
        pautas = repo.list_editorial_pautas_pool(
            status="ARQUIVADO",
            categoria=cat if cat != "all" else None,
            search=q,
            limit=100,
        )
    else:  # backlog
        pautas = repo.list_editorial_pautas_pool(
            status="DISPONIVEL",
            categoria=cat if cat != "all" else None,
            search=q,
            limit=100,
        )

    return HTMLResponse(
        content=_render_hub_page(
            stats=stats,
            pautas=pautas,
            active_tab=tab,
            active_cat=cat,
            search_query=q or "",
            msg=msg or "",
        )
    )


@router.post("/hub/suggest")
async def hub_suggest_pauta(
    titulo: str = Form(...),
    categoria: str = Form("Tecnologia da Informação (TI)"),
    angulo_editorial: Optional[str] = Form(None),
    fontes: Optional[str] = Form(None),
    action_mode: str = Form("save"),
):
    """
    Submits a manual topic suggestion either to save in the backlog or to produce immediately.
    """
    clean_titulo = titulo.strip()
    if not clean_titulo:
        return RedirectResponse(
            "/api/v1/editorial/hub?tab=suggest&msg=error_empty",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    fontes_list = [f.strip() for f in fontes.split("\n") if f.strip()] if fontes else []

    saved_pauta = repo.save_editorial_pauta(
        titulo=clean_titulo,
        categoria=categoria,
        origem="MANUAL",
        angulo_editorial=angulo_editorial.strip() if angulo_editorial else None,
        sintese_factual=f"Tema proposto manualmente pelo autor em {datetime.now().strftime('%d/%m/%Y %H:%M')}.",
        roteiro_topicos=[],
        fontes=fontes_list,
        status="DISPONIVEL",
    )

    if action_mode == "produce":
        repo.update_editorial_pauta_status(saved_pauta["id"], "EM_PRODUCAO")
        payload = {
            "pauta_id": saved_pauta["id"],
            "pauta_titulo": clean_titulo,
            "categoria": categoria,
            "target_format": "both",
            "angulo_editorial": angulo_editorial,
            "sintese_fiel_das_materias": f"Tema sugerido manualmente: {clean_titulo}. Fontes: {', '.join(fontes_list)}",
            "curation_date": datetime.now().strftime("%Y%m%d"),
        }
        logger.info("Manual topic production triggered from hub: '%s'", clean_titulo)
        async_task = task_deep_content_generation.delay(payload)
        return HTMLResponse(
            content=_render_success_html(
                html.escape(clean_titulo),
                html.escape(categoria),
                async_task.id,
            )
        )

    return RedirectResponse(
        "/api/v1/editorial/hub?tab=backlog&msg=suggest_saved",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/hub/produce/{pauta_id}", response_class=HTMLResponse)
async def hub_produce_pauta(pauta_id: int):
    """
    Triggers autonomous production for a pauta selected from the backlog.
    """
    pauta = repo.get_editorial_pauta(pauta_id)
    if not pauta:
        return HTMLResponse("Pauta não encontrada", status_code=status.HTTP_404_NOT_FOUND)

    repo.update_editorial_pauta_status(pauta_id, "EM_PRODUCAO")
    payload = {
        "pauta_id": pauta["id"],
        "pauta_titulo": pauta["titulo"],
        "categoria": pauta["categoria"],
        "target_format": "both",
        "angulo_editorial": pauta.get("angulo_editorial"),
        "sintese_fiel_das_materias": pauta.get("sintese_factual"),
        "curation_date": datetime.now().strftime("%Y%m%d"),
    }
    logger.info("Topic production triggered from backlog hub: '%s' (#%d)", pauta["titulo"], pauta_id)
    async_task = task_deep_content_generation.delay(payload)
    return HTMLResponse(
        content=_render_success_html(
            html.escape(pauta["titulo"]),
            html.escape(pauta["categoria"]),
            async_task.id,
        )
    )


@router.get("/hub/archive/{pauta_id}")
async def hub_archive_pauta(pauta_id: int):
    """
    Toggles the archived state of a pauta in the pool.
    """
    pauta = repo.get_editorial_pauta(pauta_id)
    if not pauta:
        return RedirectResponse("/api/v1/editorial/hub", status_code=status.HTTP_303_SEE_OTHER)

    if pauta["status"] == "ARQUIVADO":
        repo.update_editorial_pauta_status(pauta_id, "DISPONIVEL")
        return RedirectResponse(
            "/api/v1/editorial/hub?tab=archived&msg=restored",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    else:
        repo.update_editorial_pauta_status(pauta_id, "ARQUIVADO")
        return RedirectResponse(
            "/api/v1/editorial/hub?tab=backlog&msg=archived",
            status_code=status.HTTP_303_SEE_OTHER,
        )


def _render_linkedin_comment_approved_html(target_nome: str, post_url: str, comentario: str, task_id: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Comentário Sniper Aprovado | OmniFlow</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
        }}
        .card {{
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 16px;
            max-width: 580px;
            width: 100%;
            padding: 32px;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
        }}
        .badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: #064e3b;
            color: #34d399;
            padding: 6px 14px;
            border-radius: 9999px;
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 20px;
        }}
        h1 {{
            font-size: 24px;
            margin: 0 0 12px 0;
            color: #ffffff;
        }}
        p {{
            color: #94a3b8;
            line-height: 1.6;
            margin: 0 0 20px 0;
        }}
        .preview-box {{
            background: #0f172a;
            border: 1px solid #334155;
            border-radius: 10px;
            padding: 16px;
            margin-bottom: 24px;
        }}
        .preview-label {{
            font-size: 11px;
            color: #64748b;
            text-transform: uppercase;
            font-weight: 700;
            margin-bottom: 8px;
        }}
        .comment-text {{
            font-size: 14px;
            color: #e2e8f0;
            line-height: 1.5;
            white-space: pre-wrap;
        }}
        .btn {{
            display: inline-block;
            background: #0077b5;
            color: #ffffff;
            text-decoration: none;
            padding: 12px 24px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
            transition: background 0.2s;
        }}
        .btn:hover {{
            background: #005582;
        }}
        .footer {{
            margin-top: 20px;
            font-size: 12px;
            color: #64748b;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="badge">
            <span>✓</span> APROVADO & EM PUBLICAÇÃO
        </div>
        <h1>Comentário Sniper em Andamento</h1>
        <p>O comentário foi aprovado com sucesso! O worker do Playwright está digitando de forma humanizada e publicando no post de <strong>{html.escape(target_nome)}</strong>.</p>
        
        <div class="preview-box">
            <div class="preview-label">Comentário Aprovado:</div>
            <div class="comment-text">{html.escape(comentario)}</div>
        </div>

        <a href="{html.escape(post_url)}" target="_blank" class="btn">Ver Post no LinkedIn ↗</a>
        
        <div class="footer">
            Task Celery ID: {html.escape(task_id)} • Confirmação será enviada via WhatsApp.
        </div>
    </div>
</body>
</html>"""


@router.get("/linkedin-comment/approve", response_class=HTMLResponse)
async def approve_linkedin_comment(token: str = Query(..., description="Action token for sniper comment approval")):
    """
    Callback endpoint triggered by 1-click WhatsApp/email approval.
    Verifies HMAC/Redis token, dispatches Playwright publication task, and returns confirmation screen.
    """
    from flows.flow_linkedin_growth import task_publish_approved_linkedin_comment

    payload = verify_linkedin_comment_action_token(token)
    if not payload:
        return HTMLResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content="""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#0f172a;color:#fff;display:flex;align-items:center;justify-content:center;height:100vh;">
            <div style="background:#1e293b;padding:32px;border-radius:12px;max-width:480px;text-align:center;">
            <h2 style="color:#ef4444;">Token Inválido ou Expirado</h2>
            <p style="color:#94a3b8;">O link de aprovação deste comentário expirou ou já foi utilizado anteriormente.</p>
            </div></body></html>""",
        )

    comment_id = payload.get("comment_id")
    comment = repo.get_linkedin_growth_comment_by_id(comment_id)
    if not comment:
        return HTMLResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content="""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#0f172a;color:#fff;display:flex;align-items:center;justify-content:center;height:100vh;">
            <div style="background:#1e293b;padding:32px;border-radius:12px;max-width:480px;text-align:center;">
            <h2 style="color:#f59e0b;">Comentário Não Encontrado</h2>
            <p style="color:#94a3b8;">O registro deste comentário não foi localizado no banco de dados.</p>
            </div></body></html>""",
        )

    # Dispatch Celery background task
    async_task = task_publish_approved_linkedin_comment.delay(comment_id)
    repo.update_linkedin_growth_comment_status(comment_id, "APPROVED")

    return HTMLResponse(
        content=_render_linkedin_comment_approved_html(
            target_nome=comment["target_nome"],
            post_url=comment["post_url"],
            comentario=comment["comentario_gerado"],
            task_id=async_task.id,
        )
    )


@router.get("/linkedin-comment/like", response_class=HTMLResponse)
async def like_linkedin_post_from_token(token: str = Query(..., description="Action token for liking post")):
    """
    1-click reaction link from WhatsApp/email to only like the LinkedIn post.
    """
    from flows.flow_linkedin_growth import task_like_approved_linkedin_post

    payload = verify_linkedin_comment_action_token(token)
    if not payload:
        return HTMLResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content="""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#0f172a;color:#fff;display:flex;align-items:center;justify-content:center;height:100vh;">
            <div style="background:#1e293b;padding:32px;border-radius:12px;max-width:480px;text-align:center;">
            <h2 style="color:#ef4444;">Token Inválido ou Expirado</h2>
            <p style="color:#94a3b8;">O link para curtir esta publicação expirou ou já foi utilizado.</p>
            </div></body></html>""",
        )

    comment_id = payload.get("comment_id")
    comment = repo.get_linkedin_growth_comment_by_id(comment_id)
    if not comment:
        return HTMLResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content="""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#0f172a;color:#fff;display:flex;align-items:center;justify-content:center;height:100vh;">
            <div style="background:#1e293b;padding:32px;border-radius:12px;max-width:480px;text-align:center;">
            <h2 style="color:#f59e0b;">Publicação Não Encontrada</h2>
            <p style="color:#94a3b8;">O registro deste post não foi localizado no banco de dados.</p>
            </div></body></html>""",
        )

    repo.update_linkedin_growth_comment_status(comment_id, "LIKING")
    async_task = task_like_approved_linkedin_post.delay(comment_id)

    return HTMLResponse(
        content=f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Post Curtido | OmniFlow LinkedIn Sniper</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
        }}
        .card {{
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 16px;
            padding: 32px;
            max-width: 520px;
            width: 100%;
            text-align: center;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
        }}
        .icon {{
            font-size: 48px;
            margin-bottom: 16px;
        }}
        h1 {{
            font-size: 24px;
            margin: 0 0 8px 0;
            color: #38bdf8;
        }}
        p {{
            color: #94a3b8;
            font-size: 15px;
            line-height: 1.5;
            margin: 0 0 20px 0;
        }}
        .btn {{
            display: inline-block;
            background: #0284c7;
            color: white;
            text-decoration: none;
            padding: 12px 24px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">👍</div>
        <h1>Publicação Marcada para Curtir!</h1>
        <p>O Playwright está acessando o LinkedIn para curtir o post de <strong>{html.escape(comment['target_nome'])}</strong> sem publicar comentários.</p>
        <a href="{html.escape(comment['post_url'])}" target="_blank" class="btn">Abrir Post no LinkedIn ↗</a>
    </div>
</body>
</html>"""
    )



@router.get("/linkedin-targets")
async def list_linkedin_targets():
    """
    Returns all targets and general statistics for LinkedIn Sniper Growth.
    """
    targets = repo.get_all_linkedin_targets(limit=100)
    stats = repo.get_linkedin_growth_stats()
    return {
        "status": "SUCCESS",
        "stats": stats,
        "total": len(targets),
        "targets": targets,
    }


class LinkedInTargetCreate(BaseModel):
    nome: str
    nicho: str = "TI_JURIDICO"
    linkedin_url: str
    descricao: Optional[str] = None


@router.post("/linkedin-targets")
async def create_linkedin_target(payload: LinkedInTargetCreate):
    """
    Creates a new monitored LinkedIn target.
    """
    if not payload.nome or not payload.linkedin_url:
        raise HTTPException(status_code=400, detail="Nome e URL do LinkedIn são obrigatórios.")
    target_id = repo.add_linkedin_target(
        nome=payload.nome,
        nicho=payload.nicho,
        linkedin_url=payload.linkedin_url,
        descricao=payload.descricao,
    )
    return {"status": "SUCCESS", "target_id": target_id}


@router.post("/linkedin-targets/{target_id}/toggle")
async def toggle_linkedin_target(target_id: int):
    """
    Toggles active/inactive state of a target.
    """
    ok = repo.toggle_linkedin_target(target_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Alvo não encontrado.")
    return {"status": "SUCCESS", "target_id": target_id}


@router.delete("/linkedin-targets/{target_id}")
async def delete_linkedin_target(target_id: int):
    """
    Deletes a monitored target.
    """
    ok = repo.delete_linkedin_target(target_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Alvo não encontrado.")
    return {"status": "SUCCESS", "target_id": target_id}


@router.get("/linkedin-comments")
async def list_linkedin_comments():
    """
    Returns pending and recent sniper comments generated for approval.
    """
    pending = repo.get_pending_linkedin_growth_comments(limit=50)
    all_comments = repo.get_all_linkedin_growth_comments(limit=50)
    stats = repo.get_linkedin_growth_stats()
    return {
        "status": "SUCCESS",
        "stats": stats,
        "pending_count": len(pending),
        "pending_comments": pending,
        "comments": all_comments,
    }


@router.post("/linkedin-comments/{comment_id}/approve")
async def direct_approve_linkedin_comment(comment_id: int):
    """
    Directly approves and enqueues publication of a sniper comment from authenticated dashboard.
    """
    from flows.flow_linkedin_growth import task_publish_approved_linkedin_comment
    comment = repo.get_linkedin_growth_comment_by_id(comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail="Comentário não encontrado.")
    
    async_task = task_publish_approved_linkedin_comment.delay(comment_id)
    repo.update_linkedin_growth_comment_status(comment_id, "APPROVED")
    return {
        "status": "SUCCESS",
        "task_id": async_task.id,
        "comment_id": comment_id,
        "message": "Comentário aprovado! Publicação em andamento pelo Playwright.",
    }


@router.post("/linkedin-comments/{comment_id}/like")
async def direct_like_linkedin_post(comment_id: int):
    """
    Directly likes the target post without publishing a comment.
    """
    from flows.flow_linkedin_growth import task_like_approved_linkedin_post
    comment = repo.get_linkedin_growth_comment_by_id(comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail="Comentário não encontrado.")

    repo.update_linkedin_growth_comment_status(comment_id, "LIKING")
    async_task = task_like_approved_linkedin_post.delay(comment_id)
    return {
        "status": "SUCCESS",
        "task_id": async_task.id,
        "comment_id": comment_id,
        "message": "Reação de Curtir enviada! Execução em andamento pelo Playwright.",
    }


class LinkedInCommentEditRequest(BaseModel):
    comentario: str


@router.post("/linkedin-comments/{comment_id}/edit")
async def edit_linkedin_comment(comment_id: int, payload: LinkedInCommentEditRequest):
    """
    Updates the text of a pending sniper comment and saves to style memory.
    """
    comment = repo.get_linkedin_growth_comment_by_id(comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail="Comentário não encontrado.")
    
    if not payload.comentario or not payload.comentario.strip():
        raise HTTPException(status_code=400, detail="O comentário não pode ser vazio.")
        
    repo.update_linkedin_growth_comment_text(comment_id, payload.comentario.strip())
    repo.record_style_memory(comment_id, payload.comentario.strip(), foi_editado=True)
    
    updated = repo.get_linkedin_growth_comment_by_id(comment_id)
    return {
        "status": "SUCCESS",
        "comment": updated,
        "message": "Comentário atualizado e memória de estilo salva!",
    }


class LinkedInCommentRefineRequest(BaseModel):
    instrucao: str


@router.post("/linkedin-comments/{comment_id}/refine")
async def refine_linkedin_comment_with_ai(comment_id: int, payload: LinkedInCommentRefineRequest):
    """
    Refines a comment suggestion using Gemini based on human instruction.
    """
    from flows.flow_linkedin_growth import refine_sniper_comment
    comment = repo.get_linkedin_growth_comment_by_id(comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail="Comentário não encontrado.")

    if not payload.instrucao or not payload.instrucao.strip():
        raise HTTPException(status_code=400, detail="A instrução de refinamento é obrigatória.")

    refinement = refine_sniper_comment(
        post_text=comment["post_texto"],
        current_comment=comment["comentario_gerado"],
        instruction=payload.instrucao.strip(),
        author_name=comment["target_nome"],
    )

    repo.update_linkedin_growth_comment_text(comment_id, refinement.comentario)
    repo.record_style_memory(comment_id, refinement.comentario, instrucao=payload.instrucao.strip())
    updated = repo.get_linkedin_growth_comment_by_id(comment_id)
    return {
        "status": "SUCCESS",
        "comment": updated,
        "new_comentario": refinement.comentario,
        "tese_central": refinement.tese_central,
        "message": "Comentário refinado pela IA com sucesso!",
    }


@router.post("/linkedin-comments/{comment_id}/reject")
async def direct_reject_linkedin_comment(comment_id: int):
    """
    Rejects a sniper comment.
    """
    comment = repo.get_linkedin_growth_comment_by_id(comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail="Comentário não encontrado.")
    
    repo.update_linkedin_growth_comment_status(comment_id, "REJECTED")
    return {
        "status": "SUCCESS",
        "comment_id": comment_id,
        "message": "Comentário rejeitado.",
    }


@router.post("/linkedin-radar/trigger")
async def trigger_linkedin_radar():
    """
    Triggers an immediate scan of active targets via Celery.
    """
    from flows.flow_linkedin_growth import task_linkedin_sniper_radar
    async_task = task_linkedin_sniper_radar.delay(batch_size=4)
    return {
        "status": "SUCCESS",
        "task_id": async_task.id,
        "message": "Radar disparado com sucesso em segundo plano!",
    }


# ─── LinkedIn Connection Autopilot Endpoints ─────────────────────────────────

@router.get("/linkedin-autopilot")
async def get_linkedin_autopilot_overview():
    """
    Returns stats and recent prospects for LinkedIn Connection Autopilot.
    """
    stats = repo.get_autopilot_stats()
    pending = repo.get_pending_autopilot_prospects(limit=30)
    recent = repo.get_all_autopilot_prospects(limit=50)
    return {
        "status": "SUCCESS",
        "stats": stats,
        "pending": pending,
        "recent": recent,
    }


@router.post("/linkedin-autopilot/discover")
async def trigger_linkedin_autopilot_discovery():
    """
    Triggers an immediate discovery run across active LinkedIn targets to harvest ICP prospects.
    """
    from flows.flow_linkedin_autopilot import task_linkedin_autopilot_discovery
    async_task = task_linkedin_autopilot_discovery.delay(batch_targets=3)
    return {
        "status": "SUCCESS",
        "task_id": async_task.id,
        "message": "Discovery do Autopilot disparado em segundo plano!",
    }


@router.post("/linkedin-autopilot/invite")
async def trigger_linkedin_autopilot_inviter(batch_size: int = 4):
    """
    Triggers an immediate safe batch of connection invites to pending prospects.
    """
    from flows.flow_linkedin_autopilot import task_linkedin_autopilot_inviter
    async_task = task_linkedin_autopilot_inviter.delay(batch_size=batch_size, daily_limit=15)
    return {
        "status": "SUCCESS",
        "task_id": async_task.id,
        "message": f"Disparo de convites do Autopilot ({batch_size} perfis) iniciado em segundo plano!",
    }


@router.post("/linkedin-autopilot/prospects/{prospect_id}/skip")
async def skip_linkedin_autopilot_prospect(prospect_id: int):
    """
    Skips a discovered prospect from receiving connection invites.
    """
    ok = repo.update_autopilot_prospect_status(prospect_id=prospect_id, status="SKIPPED")
    if not ok:
        raise HTTPException(status_code=404, detail="Prospect not found.")
    return {
        "status": "SUCCESS",
        "prospect_id": prospect_id,
        "message": "Prospect marcado como pulado.",
    }



