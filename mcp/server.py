#!/usr/bin/env python3
"""
OmniFlow Lead Enrichment - Model Context Protocol (MCP) Server.
Enables AI assistants (Claude Desktop, Cursor, Cline, Antigravity) to directly
enrich companies, retrieve cadastral/Google data, and find decision-makers on LinkedIn.

Standard: MCP Protocol Version 2024-11-05 (JSON-RPC 2.0 over stdio).
Zero external dependencies: runs on pure Python 3.8+ standard library.
"""

import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Union

# Logging configured to stderr so stdout remains clean JSON-RPC protocol stream
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [MCP] [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp_enrichment_server")

# Default environment configuration
def _get_default_api_url() -> str:
    env_url = os.environ.get("OMNIFLOW_API_URL")
    if env_url:
        return env_url.rstrip("/")
    if os.path.exists("/.dockerenv") or os.environ.get("SERVICE_TYPE") == "api":
        return "http://127.0.0.1:8000"
    return "https://api.fernandonogueira.dev.br"

DEFAULT_API_URL = _get_default_api_url()
DEFAULT_API_KEY = os.environ.get(
    "OMNIFLOW_API_KEY",
    "omniflow_232750db9cac2682c20ffadd0bce268f2d85764bc1149921",
)

SERVER_INFO = {
    "name": "omniflow-lead-enrichment",
    "version": "1.0.0",
}

PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_PROTOCOL_VERSIONS = {
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
    "2026-07-28",
}

# Tool Definitions for MCP Clients
TOOLS = [
    {
        "name": "enrich_company",
        "description": (
            "Enriquece uma empresa a partir do nome utilizando IA (Gemini), Google Search e LinkedIn. "
            "Retorna: (1) Dados cadastrais oficiais da Receita (Razão Social, CNPJ formatado, Situação Cadastral, Sede), "
            "(2) Website oficial, e-mails, telefones e portfólio de produtos/serviços, "
            "(3) Perfil corporativo da empresa no LinkedIn (slogan, faixa de colaboradores, ano de fundação, especialidades e vagas em aberto), "
            "(4) Lista de tomadores de decisão (Sócios, C-Level, Diretores, Heads) com links do LinkedIn e checagem de vínculo atual, "
            "(5) Diagnóstico de mercado, dor resolvida, sugestão prática de pitch comercial e recomendação de melhor contato."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Nome da empresa a enriquecer (ex: 'Matera', 'Nubank', 'Totvs')",
                },
                "location": {
                    "type": "string",
                    "description": "Cidade ou Estado para auxiliar na desambiguação se o nome for comum (ex: 'Campinas SP', 'São Paulo')",
                },
                "deep_scrape": {
                    "type": "boolean",
                    "description": "Se true, faz scraping aprofundado do site oficial para extrair produtos e serviços reais",
                    "default": True,
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "find_decision_makers",
        "description": (
            "Busca profissionais em cargos de gestão e tomada de decisão (Sócios, C-Level, Diretores, Heads) "
            "no LinkedIn para uma empresa específica via Google Dorking anônimo e auditoria de vínculo por IA. "
            "Retorna nomes, cargos atuais, níveis hierárquicos, departamento, links de perfil e análise de melhor ponto de abordagem."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "company_name": {
                    "type": "string",
                    "description": "Nome da empresa a pesquisar decisores (ex: 'Matera')",
                },
                "website_or_domain": {
                    "type": "string",
                    "description": "Domínio oficial da empresa se conhecido (ex: 'matera.com') para aumentar a precisão",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Quantidade máxima de perfis a retornar (padrão: 10, máx: 25)",
                    "default": 10,
                },
            },
            "required": ["company_name"],
        },
    },
    {
        "name": "get_linkedin_company_profile",
        "description": (
            "Extrai exclusivamente os dados institucionais da página corporativa da empresa no LinkedIn (Company Page). "
            "Retorna URL oficial, slogan/tagline, resumo institucional, setor oficial, faixa de colaboradores (ex: 501-1.000 funcionários), "
            "total aproximado de seguidores, sede, ano de fundação, especialidades e URL de vagas ativas (/jobs)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Nome da empresa a pesquisar",
                },
                "location": {
                    "type": "string",
                    "description": "Localização/Cidade para auxílio na busca",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "verify_email",
        "description": (
            "Verifica ativamente a existência e entregabilidade de uma caixa postal corporativa "
            "através de um handshake SMTP direto (zero-bounce: EHLO -> MAIL FROM -> RCPT TO -> QUIT). "
            "Confirma se o e-mail do executivo realmente existe e recebe mensagens sem enviar e-mails de verdade."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Endereço de e-mail corporativo a validar (ex: 'alessio.mainardi@zucchetti.com')",
                },
            },
            "required": ["email"],
        },
    },
]


def _http_request(
    endpoint: str,
    method: str = "GET",
    data: Optional[Dict[str, Any]] = None,
    query_params: Optional[Dict[str, Any]] = None,
    timeout: int = 120,
) -> Dict[str, Any]:
    """Helper that executes authenticated HTTP requests to the OmniFlow backend."""
    api_url = os.environ.get("OMNIFLOW_API_URL", DEFAULT_API_URL).rstrip("/")
    api_key = os.environ.get("OMNIFLOW_API_KEY", DEFAULT_API_KEY)

    url = f"{api_url}{endpoint}"
    if query_params:
        filtered = {k: v for k, v in query_params.items() if v is not None}
        if filtered:
            url += f"?{urllib.parse.urlencode(filtered)}"

    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    if api_key:
        headers["X-API-Key"] = api_key

    body = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            resp_body = response.read().decode("utf-8")
            return json.loads(resp_body)
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        logger.error("HTTP error calling %s: %s - %s", url, e.code, err_msg)
        try:
            parsed = json.loads(err_msg)
            return {"error": True, "status_code": e.code, "detail": parsed.get("detail", err_msg)}
        except Exception:
            return {"error": True, "status_code": e.code, "detail": err_msg}
    except Exception as e:
        logger.error("Network or unexpected error calling %s: %s", url, e)
        return {"error": True, "detail": str(e)}


def handle_initialize(req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Handles MCP initialize handshake."""
    logger.info("Initializing MCP connection from client: %s", params.get("clientInfo"))
    client_proto = params.get("protocolVersion")
    protocol_version = client_proto if (client_proto in SUPPORTED_PROTOCOL_VERSIONS or client_proto) else PROTOCOL_VERSION
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "protocolVersion": protocol_version,
            "capabilities": {
                "tools": {
                    "listChanged": False,
                },
                "resources": {
                    "subscribe": False,
                    "listChanged": False,
                },
                "prompts": {
                    "listChanged": False,
                },
            },
            "serverInfo": SERVER_INFO,
        },
    }


def handle_tools_list(req_id: Any) -> Dict[str, Any]:
    """Returns available tools list to the MCP client."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "tools": TOOLS,
        },
    }


def handle_resources_list(req_id: Any) -> Dict[str, Any]:
    """Returns empty resources list for clients discovering resources."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "resources": [],
        },
    }


def handle_prompts_list(req_id: Any) -> Dict[str, Any]:
    """Returns empty prompts list for clients discovering prompts."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "prompts": [],
        },
    }


def handle_tools_call(req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatches tool execution to the OmniFlow backend."""
    tool_name = params.get("name")
    args = params.get("arguments", {})

    logger.info("Executing tool '%s' with arguments: %s", tool_name, args)

    try:
        if tool_name == "enrich_company":
            company_name = args.get("name") or args.get("company_name") or args.get("empresa")
            if not company_name:
                raise ValueError("Parâmetro 'name' é obrigatório.")
            payload = {
                "name": company_name,
                "location": args.get("location"),
                "deep_scrape": args.get("deep_scrape", True),
            }
            res = _http_request("/api/v1/enrich", method="POST", data=payload, timeout=240)

        elif tool_name == "find_decision_makers":
            company_name = args.get("company_name") or args.get("name")
            if not company_name:
                raise ValueError("Parâmetro 'company_name' é obrigatório.")
            payload = {
                "company_name": company_name,
                "website_or_domain": args.get("website_or_domain"),
                "max_results": args.get("max_results", 10),
            }
            res = _http_request("/api/v1/enrich/decision-makers", method="POST", data=payload, timeout=180)

        elif tool_name == "get_linkedin_company_profile":
            name = args.get("name") or args.get("company_name")
            if not name:
                raise ValueError("Parâmetro 'name' é obrigatório.")
            payload = {
                "name": name,
                "location": args.get("location"),
            }
            res = _http_request("/api/v1/enrich/linkedin/company", method="POST", data=payload, timeout=120)

        elif tool_name == "verify_email":
            email = args.get("email")
            if not email:
                raise ValueError("Parâmetro 'email' é obrigatório.")
            payload = {
                "email": email,
            }
            res = _http_request("/api/v1/enrich/verify-email", method="POST", data=payload, timeout=30)

        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Ferramenta desconhecida: '{tool_name}'",
                },
            }

        # Format content for MCP LLM response
        text_content = json.dumps(res, ensure_ascii=False, indent=2)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": text_content,
                    }
                ],
                "isError": bool(res.get("error", False)),
            },
        }

    except Exception as e:
        logger.error("Error executing tool '%s': %s", tool_name, e)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": f"Erro ao executar a ferramenta {tool_name}: {str(e)}",
                    }
                ],
                "isError": True,
            },
        }


def process_single_message(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Processes a single JSON-RPC message dictionary."""
    if not isinstance(msg, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request: Expected a JSON object"},
        }

    req_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params", {})

    # Handle notifications (no response needed for notifications)
    if method in (
        "notifications/initialized",
        "notifications/cancelled",
        "notifications/progress",
        "notifications/message",
        "logging/setLevel",
    ):
        logger.info("Client MCP notification received: %s", method)
        return None

    if method and method.startswith("notifications/"):
        logger.info("Client notification received: %s", method)
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "initialize":
        return handle_initialize(req_id, params)

    if method == "tools/list":
        return handle_tools_list(req_id)

    if method == "tools/call":
        return handle_tools_call(req_id, params)

    if method == "resources/list":
        return handle_resources_list(req_id)

    if method == "prompts/list":
        return handle_prompts_list(req_id)

    # Unknown method
    if req_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}",
            },
        }

    return None


def process_message(data: Any) -> Optional[Union[Dict[str, Any], List[Dict[str, Any]]]]:
    """Parses JSON-RPC input (string, dict, or batch list) and routes to the appropriate handler."""
    if isinstance(data, (dict, list)):
        parsed = data
    elif isinstance(data, str):
        line = data.strip()
        if not line:
            return None
        try:
            parsed = json.loads(line)
        except Exception as e:
            logger.error("Failed to parse JSON-RPC message: %s", e)
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error: Invalid JSON"},
            }
    else:
        return None

    if isinstance(parsed, list):
        # JSON-RPC 2.0 batch request
        responses = []
        for item in parsed:
            resp = process_single_message(item)
            if resp is not None:
                responses.append(resp)
        return responses if responses else None

    return process_single_message(parsed)


def run_stdio_server():
    """Main loop for stdio transport."""
    logger.info("OmniFlow Lead Enrichment MCP Server started on stdio.")
    logger.info("Connected API: %s", os.environ.get("OMNIFLOW_API_URL", DEFAULT_API_URL))

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break

            response = process_message(line)
            if response is not None:
                out = json.dumps(response, ensure_ascii=False)
                sys.stdout.write(out + "\n")
                sys.stdout.flush()

        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            logger.error("Unexpected error in main stdio loop: %s", e)


if __name__ == "__main__":
    run_stdio_server()
