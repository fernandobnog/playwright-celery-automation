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
from typing import Any, Dict, Optional
from fastapi import APIRouter, Query, status
from fastapi.responses import HTMLResponse
from redis import Redis

from core.config import settings
from core.security import verify_editorial_action_token
from flows.flow_content_deep_writer import task_deep_content_generation

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

    return HTMLResponse(
        content=_render_success_html(escaped_title, escaped_cat, async_task.id)
    )
