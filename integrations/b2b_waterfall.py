"""
B2B Enrichment Waterfall Module with Free Tier Rotation.
Orchestrates a cascading fallback across multiple B2B intelligence providers:
Apollo.io -> Hunter.io -> Snov.io -> People Data Labs -> Kaspr -> Lusha -> SalesQL.

Features:
- Automatic rotation on quota exhaustion (HTTP 429, 402, 403, or credit limits)
- Graceful degradation (0ms overhead if API keys are unconfigured)
- Normalization of emails, phone numbers, and LinkedIn profiles
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import httpx

from core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class B2BContactResult:
    provider: str
    success: bool
    email: Optional[str] = None
    email_status: Optional[str] = None
    pattern: Optional[str] = None
    phones: List[str] = field(default_factory=list)
    linkedin_url: Optional[str] = None
    job_title: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


def _split_first_last(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def query_apollo(
    full_name: str,
    company_name: str,
    domain: Optional[str] = None,
    timeout: float = 5.0,
) -> Optional[B2BContactResult]:
    """Queries Apollo.io Person Match API."""
    api_key = settings.APOLLO_API_KEY
    if not api_key:
        return None

    first, last = _split_first_last(full_name)
    url = "https://api.apollo.io/v1/people/match"
    payload: Dict[str, Any] = {
        "api_key": api_key,
        "name": full_name,
        "first_name": first,
        "last_name": last,
        "organization_name": company_name,
    }
    if domain:
        payload["domain"] = domain

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers={"Content-Type": "application/json"})
            if resp.status_code == 200:
                data = resp.json()
                person = data.get("person") or {}
                email = person.get("email")
                phones = []
                for p in person.get("phone_numbers", []):
                    num = p.get("sanitized_number") or p.get("raw_number")
                    if num and num not in phones:
                        phones.append(num)

                return B2BContactResult(
                    provider="apollo",
                    success=bool(email or phones),
                    email=email,
                    email_status=person.get("email_status", "verified" if email else None),
                    phones=phones,
                    linkedin_url=person.get("linkedin_url"),
                    job_title=person.get("title"),
                    raw=person,
                )
            elif resp.status_code in (429, 402, 403):
                logger.warning("Apollo.io quota reached or restricted (HTTP %d); rotating...", resp.status_code)
                return None
    except Exception as e:
        logger.debug("Apollo.io query error for %s: %s", full_name, e)
    return None


def query_hunter(
    full_name: str,
    company_name: str,
    domain: Optional[str] = None,
    timeout: float = 5.0,
) -> Optional[B2BContactResult]:
    """Queries Hunter.io Email Finder API."""
    api_key = settings.HUNTER_API_KEY
    if not api_key or not domain:
        return None

    first, last = _split_first_last(full_name)
    url = "https://api.hunter.io/v2/email-finder"
    params = {
        "domain": domain,
        "first_name": first,
        "last_name": last,
        "company": company_name,
        "api_key": api_key,
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json().get("data") or {}
                email = data.get("email")
                return B2BContactResult(
                    provider="hunter",
                    success=bool(email),
                    email=email,
                    email_status=f"score_{data.get('score', 0)}",
                    linkedin_url=data.get("linkedin_url"),
                    job_title=data.get("position"),
                    raw=data,
                )
            elif resp.status_code in (429, 402):
                logger.warning("Hunter.io quota reached (HTTP %d); rotating...", resp.status_code)
                return None
    except Exception as e:
        logger.debug("Hunter.io query error for %s: %s", full_name, e)
    return None


def query_snov(
    full_name: str,
    company_name: str,
    domain: Optional[str] = None,
    timeout: float = 5.0,
) -> Optional[B2BContactResult]:
    """Queries Snov.io Email Finder API."""
    client_id = settings.SNOV_CLIENT_ID
    client_secret = settings.SNOV_CLIENT_SECRET
    if not client_id or not client_secret or not domain:
        return None

    first, last = _split_first_last(full_name)
    try:
        with httpx.Client(timeout=timeout) as client:
            # 1. Obtain OAuth Token
            token_resp = client.post(
                "https://api.snov.io/v1/oauth/access_token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            if token_resp.status_code != 200:
                logger.warning("Snov.io auth failed: %s", token_resp.text)
                return None
            token = token_resp.json().get("access_token")

            # 2. Get Emails from Names
            resp = client.post(
                "https://api.snov.io/v1/get-emails-from-names",
                data={
                    "access_token": token,
                    "domain": domain,
                    "firstName": first,
                    "lastName": last,
                },
            )
            if resp.status_code == 200:
                data = resp.json().get("data") or {}
                emails_list = data.get("emails", [])
                best_email = emails_list[0].get("email") if emails_list else None
                return B2BContactResult(
                    provider="snov",
                    success=bool(best_email),
                    email=best_email,
                    raw=data,
                )
    except Exception as e:
        logger.debug("Snov.io query error for %s: %s", full_name, e)
    return None


def query_pdl(
    full_name: str,
    company_name: str,
    domain: Optional[str] = None,
    timeout: float = 5.0,
) -> Optional[B2BContactResult]:
    """Queries People Data Labs (PDL) Person Enrichment API."""
    api_key = settings.PDL_API_KEY
    if not api_key:
        return None

    url = "https://api.peopledatalabs.com/v5/person/enrich"
    headers = {"X-Api-Key": api_key, "Accept": "application/json"}
    params = {"name": full_name, "company": company_name}
    if domain:
        params["domain"] = domain

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=headers, params=params)
            if resp.status_code == 200:
                data = resp.json().get("data") or {}
                email = data.get("work_email") or (data.get("emails", [{}])[0].get("address") if data.get("emails") else None)
                phones = [p.get("number") for p in data.get("phone_numbers", []) if p.get("number")]
                if data.get("mobile_phone") and data["mobile_phone"] not in phones:
                    phones.insert(0, data["mobile_phone"])

                return B2BContactResult(
                    provider="pdl",
                    success=bool(email or phones),
                    email=email,
                    phones=phones,
                    linkedin_url=data.get("linkedin_url"),
                    job_title=data.get("job_title"),
                    raw=data,
                )
            elif resp.status_code in (429, 402):
                logger.warning("People Data Labs quota reached (HTTP %d); rotating...", resp.status_code)
                return None
    except Exception as e:
        logger.debug("PDL query error for %s: %s", full_name, e)
    return None


def query_kaspr(
    full_name: str,
    company_name: str,
    domain: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    timeout: float = 5.0,
) -> Optional[B2BContactResult]:
    """Queries Kaspr.io Profile Enrichment API."""
    api_key = settings.KASPR_API_KEY
    if not api_key:
        return None

    url = "https://api.kaspr.io/v1/profile"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload: Dict[str, Any] = {"name": full_name, "company": company_name}
    if linkedin_url:
        payload["linkedinUrl"] = linkedin_url

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                email = data.get("email") or data.get("work_email")
                phones = data.get("phones", [])
                return B2BContactResult(
                    provider="kaspr",
                    success=bool(email or phones),
                    email=email,
                    phones=phones,
                    linkedin_url=data.get("linkedinUrl") or linkedin_url,
                    raw=data,
                )
            elif resp.status_code in (429, 402):
                logger.warning("Kaspr quota reached (HTTP %d); rotating...", resp.status_code)
                return None
    except Exception as e:
        logger.debug("Kaspr query error for %s: %s", full_name, e)
    return None


def query_lusha(
    full_name: str,
    company_name: str,
    domain: Optional[str] = None,
    timeout: float = 5.0,
) -> Optional[B2BContactResult]:
    """Queries Lusha Person Enrichment API."""
    api_key = settings.LUSHA_API_KEY
    if not api_key:
        return None

    first, last = _split_first_last(full_name)
    url = "https://api.lusha.com/person"
    headers = {"api_key": api_key}
    params = {"firstName": first, "lastName": last, "company": company_name}
    if domain:
        params["domain"] = domain

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=headers, params=params)
            if resp.status_code == 200:
                data = resp.json().get("data") or {}
                emails = [e.get("email") for e in data.get("emailAddresses", []) if e.get("email")]
                phones = [p.get("phone") for p in data.get("phoneNumbers", []) if p.get("phone")]
                return B2BContactResult(
                    provider="lusha",
                    success=bool(emails or phones),
                    email=emails[0] if emails else None,
                    phones=phones,
                    raw=data,
                )
            elif resp.status_code in (429, 402):
                logger.warning("Lusha quota reached (HTTP %d); rotating...", resp.status_code)
                return None
    except Exception as e:
        logger.debug("Lusha query error for %s: %s", full_name, e)
    return None


def query_salesql(
    full_name: str,
    company_name: str,
    domain: Optional[str] = None,
    timeout: float = 5.0,
) -> Optional[B2BContactResult]:
    """Queries SalesQL Person Enrichment API."""
    api_key = settings.SALESQL_API_KEY
    if not api_key:
        return None

    url = "https://api.salesql.com/v1/person/enrich"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"name": full_name, "organization": company_name}

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                email = data.get("email")
                phones = data.get("phones", [])
                return B2BContactResult(
                    provider="salesql",
                    success=bool(email or phones),
                    email=email,
                    phones=phones,
                    raw=data,
                )
            elif resp.status_code in (429, 402):
                logger.warning("SalesQL quota reached (HTTP %d); rotating...", resp.status_code)
                return None
    except Exception as e:
        logger.debug("SalesQL query error for %s: %s", full_name, e)
    return None


def b2b_waterfall_enrich_person(
    full_name: str,
    company_name: str,
    domain: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> Optional[B2BContactResult]:
    """
    Executes a cascading waterfall across free-tier B2B enrichment APIs:
    Apollo.io -> Hunter.io -> Snov.io -> People Data Labs -> Kaspr -> Lusha -> SalesQL.

    Rotates on quota exhaustion (HTTP 429/402), errors, or missing data.
    """
    providers = [
        ("apollo", lambda: query_apollo(full_name, company_name, domain)),
        ("hunter", lambda: query_hunter(full_name, company_name, domain)),
        ("snov", lambda: query_snov(full_name, company_name, domain)),
        ("pdl", lambda: query_pdl(full_name, company_name, domain)),
        ("kaspr", lambda: query_kaspr(full_name, company_name, domain, linkedin_url=linkedin_url)),
        ("lusha", lambda: query_lusha(full_name, company_name, domain)),
        ("salesql", lambda: query_salesql(full_name, company_name, domain)),
    ]

    for p_name, p_fn in providers:
        try:
            res = p_fn()
            if res and res.success and (res.email or res.phones):
                logger.info(
                    "B2B Waterfall SUCCESS via [%s] for %s (%s): email=%s, phones=%d",
                    p_name, full_name, company_name, res.email, len(res.phones),
                )
                return res
        except Exception as e_prov:
            logger.debug("B2B provider %s execution error: %s", p_name, e_prov)
            continue

    return None


def hunter_domain_search(domain: str) -> Optional[Dict[str, Any]]:
    """
    Queries Hunter.io domain-search API to detect official corporate email pattern
    and retrieve publicly indexed corporate emails.
    """
    if not settings.HUNTER_API_KEY or not domain:
        return None
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(
                "https://api.hunter.io/v2/domain-search",
                params={"domain": domain, "api_key": settings.HUNTER_API_KEY, "limit": 10},
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                return {
                    "pattern": data.get("pattern"),
                    "organization": data.get("organization"),
                    "emails": [em.get("value") for em in data.get("emails", []) if em.get("value")],
                }
    except Exception as e:
        logger.debug("Hunter domain search error for %s: %s", domain, e)
    return None
