"""
Integration module for public Brazilian corporate registry APIs (Minha Receita & BrasilAPI).
Extracts official CNPJ, Razão Social, cadastral status, and QSA (Quadro de Sócios e Administradores).
"""

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CNPJ_REGEX = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}\/?\d{4}-?\d{2}\b")
CNPJ_URL_REGEX = re.compile(r"/(?:cnpj/)?(\d{14})(?:/|$|\.|\?)")


def extract_cnpjs_from_text(text: str) -> List[str]:
    """Finds all formatted or unformatted CNPJ numbers in text or URL strings."""
    if not text:
        return []
    clean_list = []
    # 1. Formatted or semi-formatted CNPJs
    for m in CNPJ_REGEX.findall(text):
        digits = re.sub(r"\D", "", m)
        if len(digits) == 14 and digits not in clean_list:
            clean_list.append(digits)

    # 2. Raw 14 digits in URLs (e.g. cnpj.biz/03916076000150)
    for m in CNPJ_URL_REGEX.findall(text):
        digits = re.sub(r"\D", "", m)
        if len(digits) == 14 and digits not in clean_list:
            clean_list.append(digits)

    return clean_list


def extract_cnpjs_from_search_results(results: List[Dict[str, Any]]) -> List[str]:
    """Extracts candidate CNPJs from a list of search result dictionaries (titles, snippets, URLs)."""
    candidates = []
    for item in results:
        combined = f"{item.get('title', '')} {item.get('snippet', '')} {item.get('url', '')}"
        for cnpj in extract_cnpjs_from_text(combined):
            if cnpj not in candidates:
                candidates.append(cnpj)
    return candidates


def format_cnpj(digits: str) -> str:
    """Formats 14 digits into standard XX.XXX.XXX/XXXX-XX."""
    d = re.sub(r"\D", "", digits)
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:14]}"
    return digits


def format_phone(ddd_tel: str) -> Optional[str]:
    """Formats raw digits into Brazilian phone format (XX) XXXX-XXXX or (XX) 9XXXX-XXXX."""
    if not ddd_tel:
        return None
    d = re.sub(r"\D", "", str(ddd_tel))
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    elif len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    elif len(d) >= 8:
        return str(ddd_tel).strip()
    return None


def consult_cnpj_public_api(cnpj: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
    """
    Queries Minha Receita (primary) and BrasilAPI (fallback) for official federal registry data.
    Returns structured corporate information and QSA (Sócios e Administradores).
    """
    clean_cnpj = re.sub(r"\D", "", cnpj)
    if len(clean_cnpj) != 14:
        return None

    # 1. Primary: Minha Receita (fast, open-source, full QSA)
    url_mr = f"https://minhareceita.org/{clean_cnpj}"
    try:
        req = urllib.request.Request(
            url_mr,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                qsa = []
                for s in data.get("qsa", []):
                    nome = s.get("nome_socio")
                    if nome:
                        qsa.append({
                            "nome": nome.strip().title(),
                            "cargo": s.get("qualificacao_socio", "Sócio"),
                            "faixa_etaria": s.get("faixa_etaria"),
                            "tipo": "Pessoa Física" if s.get("identificador_de_socio") == 2 else "Pessoa Jurídica",
                        })

                telefones = []
                for k in ("ddd_telefone_1", "ddd_telefone_2", "telefone", "ddd_fax"):
                    val = data.get(k)
                    if val:
                        p = format_phone(str(val))
                        if p and p not in telefones:
                            telefones.append(p)

                emails = []
                raw_email = (data.get("email") or "").strip().lower()
                if raw_email and "@" in raw_email:
                    emails.append(raw_email)

                formatted = {
                    "cnpj": format_cnpj(clean_cnpj),
                    "cnpj_limpo": clean_cnpj,
                    "razao_social": data.get("razao_social", "").strip().title(),
                    "nome_fantasia": (data.get("nome_fantasia") or "").strip().title() or None,
                    "situacao_cadastral": data.get("descricao_situacao_cadastral") or "Ativa",
                    "data_abertura": data.get("data_inicio_atividade"),
                    "cnae_fiscal_descricao": data.get("cnae_fiscal_descricao"),
                    "municipio": data.get("municipio", "").strip().title(),
                    "uf": data.get("uf", "").strip().upper(),
                    "sede": f"{data.get('municipio', '').strip().title()} - {data.get('uf', '').strip().upper()}",
                    "logradouro": data.get("logradouro"),
                    "numero": data.get("numero"),
                    "bairro": data.get("bairro"),
                    "cep": data.get("cep"),
                    "telefones": telefones,
                    "emails": emails,
                    "qsa": qsa,
                    "fonte": "Receita Federal (Minha Receita)",
                }
                logger.info("Minha Receita succeeded for CNPJ %s (%s)", formatted["cnpj"], formatted["razao_social"])
                return formatted
    except Exception as e_mr:
        logger.warning("Minha Receita API query failed for %s: %s", clean_cnpj, e_mr)

    # 2. Fallback: BrasilAPI
    url_brasilapi = f"https://brasilapi.com.br/api/cnpj/v1/{clean_cnpj}"
    try:
        req = urllib.request.Request(
            url_brasilapi,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                qsa = []
                for s in data.get("qsa", []):
                    nome = s.get("nome_socio")
                    if nome:
                        qsa.append({
                            "nome": nome.strip().title(),
                            "cargo": s.get("qualificacao_socio", "Sócio"),
                            "faixa_etaria": s.get("faixa_etaria"),
                            "tipo": "Pessoa Física",
                        })

                telefones = []
                for k in ("ddd_telefone_1", "ddd_telefone_2", "telefone", "ddd_fax"):
                    val = data.get(k)
                    if val:
                        p = format_phone(str(val))
                        if p and p not in telefones:
                            telefones.append(p)

                emails = []
                raw_email = (data.get("email") or "").strip().lower()
                if raw_email and "@" in raw_email:
                    emails.append(raw_email)

                formatted = {
                    "cnpj": format_cnpj(clean_cnpj),
                    "cnpj_limpo": clean_cnpj,
                    "razao_social": data.get("razao_social", "").strip().title(),
                    "nome_fantasia": (data.get("nome_fantasia") or "").strip().title() or None,
                    "situacao_cadastral": data.get("descricao_situacao_cadastral") or "Ativa",
                    "data_abertura": data.get("data_inicio_atividade"),
                    "cnae_fiscal_descricao": data.get("cnae_fiscal_descricao"),
                    "municipio": data.get("municipio", "").strip().title(),
                    "uf": data.get("uf", "").strip().upper(),
                    "sede": f"{data.get('municipio', '').strip().title()} - {data.get('uf', '').strip().upper()}",
                    "logradouro": data.get("logradouro"),
                    "numero": data.get("numero"),
                    "bairro": data.get("bairro"),
                    "cep": data.get("cep"),
                    "telefones": telefones,
                    "emails": emails,
                    "qsa": qsa,
                    "fonte": "Receita Federal (BrasilAPI)",
                }
                logger.info("BrasilAPI succeeded for CNPJ %s (%s)", formatted["cnpj"], formatted["razao_social"])
                return formatted
    except Exception as e_bapi:
        logger.warning("BrasilAPI query failed for %s: %s", clean_cnpj, e_bapi)

    return None
