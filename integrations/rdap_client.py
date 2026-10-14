"""
Integration module for Registro.br RDAP (Registration Data Access Protocol).
Directly extracts official CNPJ, legal representatives, corporate emails, and technical contacts
for Brazilian domains (.com.br, etc.) without requiring scraping or third-party paid APIs.
"""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import httpx

logger = logging.getLogger(__name__)


def clean_domain_for_rdap(raw_domain_or_url: str) -> Optional[str]:
    """Extracts a clean hostname without protocol, path, or www."""
    if not raw_domain_or_url:
        return None
    val = raw_domain_or_url.strip().lower()
    if "://" not in val:
        val = "https://" + val
    try:
        parsed = urlparse(val)
        host = parsed.netloc or parsed.path
        if host.startswith("www."):
            host = host[4:]
        host = host.split(":")[0]  # remove port if any
        return host if "." in host else None
    except Exception:
        return None


def extract_rdap_data(rdap_json: Dict[str, Any]) -> Dict[str, Any]:
    """Parses an RDAP JSON response from Registro.br into a structured dictionary."""
    cnpj = None
    razao_social = None
    representante_legal = rdap_json.get("legalRepresentative")
    emails: List[str] = []
    telefones: List[str] = []
    admin_tech_names: List[str] = []

    def walk_entities(entities_list: List[Dict[str, Any]]) -> None:
        nonlocal cnpj, razao_social, representante_legal
        for ent in entities_list:
            roles = ent.get("roles", [])

            # Legal representative can also be inside top entity
            if not representante_legal and ent.get("legalRepresentative"):
                representante_legal = ent.get("legalRepresentative")

            # Check publicIds for CNPJ
            for pid in ent.get("publicIds", []):
                if pid.get("type", "").lower() == "cnpj":
                    cnpj = pid.get("identifier")

            # Handle may be raw 14 digits CNPJ
            h = re.sub(r"\D", "", ent.get("handle", ""))
            if len(h) == 14 and not cnpj:
                cnpj = f"{h[:2]}.{h[2:5]}.{h[5:8]}/{h[8:12]}-{h[12:14]}"

            # Parse vCard 4.0 array
            vcard = ent.get("vcardArray", [])
            if len(vcard) >= 2 and isinstance(vcard[1], list):
                for prop in vcard[1]:
                    if not isinstance(prop, list) or len(prop) < 4:
                        continue
                    prop_name = str(prop[0]).lower()
                    prop_val = prop[3]
                    if prop_name == "fn":
                        val_str = str(prop_val).strip()
                        if "registrant" in roles:
                            razao_social = val_str
                        else:
                            if val_str and val_str not in admin_tech_names:
                                admin_tech_names.append(val_str)
                    elif prop_name == "email":
                        em = str(prop_val).strip().lower()
                        if "@" in em and em not in emails:
                            emails.append(em)
                    elif prop_name in ("tel", "voice"):
                        ph = str(prop_val).strip()
                        if ph and ph not in telefones:
                            telefones.append(ph)

            if "entities" in ent and isinstance(ent["entities"], list):
                walk_entities(ent["entities"])

    walk_entities(rdap_json.get("entities", []))

    # Regex sweep across the raw JSON payload to ensure no contact email/phone is missed
    raw_json_str = str(rdap_json)
    for em in re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', raw_json_str):
        em_clean = em.lower()
        if em_clean not in emails and not any(ign in em_clean for ign in ["registro.br", "nic.br", "iana.org"]):
            emails.append(em_clean)

    for ph in re.findall(r'\(?\d{2}\)?\s*(?:9\s*)?\d{4}[-\s]\d{4}', raw_json_str):
        if ph not in telefones:
            telefones.append(ph)

    domain = rdap_json.get("ldhName") or rdap_json.get("handle")
    return {
        "status": "SUCCESS",
        "domain": domain,
        "cnpj": cnpj,
        "razao_social": razao_social,
        "representante_legal": representante_legal,
        "emails": emails,
        "telefones": telefones,
        "admin_tech_names": admin_tech_names,
    }


def consult_rdap_registro_br(
    domain_or_url: str,
    timeout_seconds: float = 5.0,
    http_client: Optional[httpx.Client] = None,
) -> Optional[Dict[str, Any]]:
    """
    Queries https://rdap.registro.br/domain/{domain} to extract official CNPJ,
    organization name, legal representative, and administrative emails.
    Returns None if domain is not Brazilian (.br) or query fails.
    """
    clean_host = clean_domain_for_rdap(domain_or_url)
    if not clean_host:
        return None

    # RDAP Registro.br only services .br domains
    if not clean_host.endswith(".br"):
        return None

    url = f"https://rdap.registro.br/domain/{clean_host}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
        "Accept": "application/rdap+json, application/json",
    }

    try:
        if http_client:
            resp = http_client.get(url, headers=headers, timeout=timeout_seconds)
            if resp.status_code == 200:
                return extract_rdap_data(resp.json())
            return None

        with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                return extract_rdap_data(resp.json())
            logger.debug("RDAP lookup for '%s' returned status %s", clean_host, resp.status_code)
            return None
    except Exception as e:
        logger.debug("RDAP query error for '%s': %s", clean_host, e)
        return None
