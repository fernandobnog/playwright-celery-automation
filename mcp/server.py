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
from typing import Any, Dict, Optional

# Logging configured to stderr so stdout remains clean JSON-RPC protocol stream
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [MCP] [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp_enrichment_server")

# Default environment configuration
DEFAULT_API_URL = os.environ.get("OMNIFLOW_API_URL", "https://api.fernandonogueira.dev.br").rstrip("/")
DEFAULT_API_KEY = os.environ.get(
    "OMNIFLOW_API_KEY",
    "omniflow_232750db9cac2682c20ffadd0bce268f2d85764bc1149921",
)

SERVER_INFO = {
    "name": "omniflow-lead-enrichment",
    "version": "1.0.0",
}

PROTOCOL_VERSION = "2024-11-05"

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
        "Accept": "application/json",
        "User-Agent": "OmniFlow-MCP-Server/1.0",
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
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {
                    "listChanged": False,
                }
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
            res = _http_request("/api/v1/enrich", method="POST", data=payload, timeout=120)

        elif tool_name == "find_decision_makers":
            company_name = args.get("company_name") or args.get("name")
            if not company_name:
                raise ValueError("Parâmetro 'company_name' é obrigatório.")
            payload = {
                "company_name": company_name,
                "website_or_domain": args.get("website_or_domain"),
                "max_results": args.get("max_results", 10),
            }
            res = _http_request("/api/v1/enrich/decision-makers", method="POST", data=payload, timeout=90)

        elif tool_name == "get_linkedin_company_profile":
            name = args.get("name") or args.get("company_name")
            if not name:
                raise ValueError("Parâmetro 'name' é obrigatório.")
            payload = {
                "name": name,
                "location": args.get("location"),
            }
            res = _http_request("/api/v1/enrich/linkedin/company", method="POST", data=payload, timeout=60)

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


def process_message(line: str) -> Optional[Dict[str, Any]]:
    """Parses a single JSON-RPC line and routes to the appropriate handler."""
    line = line.strip()
    if not line:
        return None

    try:
        msg = json.loads(line)
    except Exception as e:
        logger.error("Failed to parse JSON-RPC message: %s", e)
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "Parse error: Invalid JSON"},
        }

    req_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params", {})

    # Handle notifications (no response needed)
    if method == "notifications/initialized":
        logger.info("Client handshake complete (notifications/initialized received)")
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "initialize":
        return handle_initialize(req_id, params)

    if method == "tools/list":
        return handle_tools_list(req_id)

    if method == "tools/call":
        return handle_tools_call(req_id, params)

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
