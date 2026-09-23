"""
Native SMTP Handshake Email Verifier.
Validates email deliverability without sending actual emails (zero-bounce verification).
Features:
- RFC 5322 syntax validation
- DNS MX host discovery
- ESMTP handshake (EHLO -> MAIL FROM -> RCPT TO -> QUIT)
- Catch-all (Accept-All) server detection
- Fast pattern discovery for corporate decision makers
- Automatic delegation to host verifier daemon when running inside Docker containers
"""

import json
import logging
import os
import random
import re
import smtplib
import socket
import string
import unicodedata
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

EMAIL_SYNTAX_REGEX = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)

DEFAULT_PROBE_SENDER = "contato@fernandonogueira.dev.br"
DEFAULT_HELO_HOST = "fernandonogueira.dev.br"

_cached_verifier_url: Optional[str] = None


def _detect_docker_gateway() -> Optional[str]:
    """Inspects kernel route table to find default bridge gateway."""
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                fields = line.strip().split()
                if len(fields) >= 3 and fields[1] == "00000000":
                    import struct
                    return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    except Exception:
        pass
    return None


def get_verifier_service_url() -> Optional[str]:
    """Discovers local or host SMTP verifier daemon URL."""
    global _cached_verifier_url
    if _cached_verifier_url is not None:
        return _cached_verifier_url if _cached_verifier_url != "" else None

    candidates = []
    env_url = os.getenv("SMTP_VERIFIER_URL")
    if env_url:
        candidates.append(env_url.rstrip("/"))

    candidates.append("http://127.0.0.1:9095")

    gw = _detect_docker_gateway()
    if gw:
        candidates.append(f"http://{gw}:9095")

    for url in candidates:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=0.2) as r:
                if r.status == 200:
                    _cached_verifier_url = url
                    return url
        except Exception:
            pass

    _cached_verifier_url = ""
    return None


def validate_email_syntax(email: str) -> bool:
    """Checks whether the email string matches standard RFC syntax."""
    if not email or len(email) > 254:
        return False
    return bool(EMAIL_SYNTAX_REGEX.match(email.strip()))


def get_mx_hosts(domain: str) -> List[str]:
    """Retrieves sorted list of MX hostnames for a domain."""
    clean_domain = domain.strip().lower()
    if clean_domain.startswith("www."):
        clean_domain = clean_domain[4:]

    mx_hosts = []

    # 1. Primary: dnspython resolver
    try:
        import dns.resolver
        resolver = dns.resolver.Resolver()
        resolver.timeout = 4.0
        resolver.lifetime = 4.0
        answers = resolver.resolve(clean_domain, "MX")
        sorted_answers = sorted(answers, key=lambda r: r.preference)
        for r in sorted_answers:
            mx_hosts.append(str(r.exchange).rstrip("."))
    except Exception as e:
        logger.debug("dnspython MX lookup failed for %s: %s", clean_domain, e)

    # 2. Fallback: socket getaddrinfo check on domain directly if no MX found
    if not mx_hosts:
        mx_hosts.append(clean_domain)

    return mx_hosts


def check_is_catch_all(
    mx_host: str,
    domain: str,
    sender: str = DEFAULT_PROBE_SENDER,
    helo_host: str = DEFAULT_HELO_HOST,
    timeout: int = 6,
) -> bool:
    """
    Checks if the mail server is configured as Catch-All (accepts any random address).
    """
    rand_user = "probe_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    bogus_email = f"{rand_user}@{domain}"

    try:
        with smtplib.SMTP(mx_host, 25, timeout=timeout) as smtp:
            smtp.ehlo_or_helo_if_needed()
            smtp.mail(sender)
            code, _ = smtp.rcpt(bogus_email)
            smtp.quit()
            return code == 250
    except Exception:
        return False


def _verify_email_smtp_direct(
    email: str,
    sender: str = DEFAULT_PROBE_SENDER,
    helo_host: str = DEFAULT_HELO_HOST,
    timeout: int = 7,
) -> Dict[str, Any]:
    """Direct in-process SMTP handshake."""
    clean_email = email.strip().lower()

    if not validate_email_syntax(clean_email):
        return {
            "email": clean_email,
            "status": "SINTAXE_INVALIDA",
            "valido": False,
            "mx_host": None,
            "smtp_code": None,
            "is_catch_all": False,
            "detalhe": "Formato de e-mail inválido conforme especificação RFC.",
        }

    domain = clean_email.split("@")[-1]

    mx_hosts = get_mx_hosts(domain)
    if not mx_hosts:
        return {
            "email": clean_email,
            "status": "SEM_MX",
            "valido": False,
            "mx_host": None,
            "smtp_code": None,
            "is_catch_all": False,
            "detalhe": f"Nenhum servidor de e-mail (MX) encontrado para o domínio @{domain}.",
        }

    last_error = None
    target_mx = mx_hosts[0]

    for mx in mx_hosts[:2]:
        try:
            with smtplib.SMTP(mx, 25, timeout=timeout) as smtp:
                smtp.ehlo(helo_host)
                mail_code, _ = smtp.mail(sender)
                if mail_code not in (250, 200):
                    last_error = f"Sender rejected with code {mail_code}"
                    continue

                rcpt_code, rcpt_msg = smtp.rcpt(clean_email)
                is_catch = False
                if rcpt_code == 250:
                    rand_bogus = "probe_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10)) + f"@{domain}"
                    catch_code, _ = smtp.rcpt(rand_bogus)
                    is_catch = (catch_code == 250)

                smtp.quit()

                msg_str = rcpt_msg.decode("utf-8", errors="ignore") if isinstance(rcpt_msg, bytes) else str(rcpt_msg)

                if rcpt_code == 250:
                    if is_catch:
                        return {
                            "email": clean_email,
                            "status": "CATCH_ALL",
                            "valido": True,
                            "mx_host": mx,
                            "smtp_code": rcpt_code,
                            "is_catch_all": True,
                            "detalhe": "Servidor aceita e-mails (Catch-All configurado; entrega provável).",
                        }
                    else:
                        return {
                            "email": clean_email,
                            "status": "VALIDADO_SMTP",
                            "valido": True,
                            "mx_host": mx,
                            "smtp_code": rcpt_code,
                            "is_catch_all": False,
                            "detalhe": "Caixa postal confirmada e ativa no servidor de correio (250 OK).",
                        }

                elif rcpt_code in (550, 551, 552, 553, 554):
                    return {
                        "email": clean_email,
                        "status": "INVALIDO",
                        "valido": False,
                        "mx_host": mx,
                        "smtp_code": rcpt_code,
                        "is_catch_all": False,
                        "detalhe": f"Endereço rejeitado pelo servidor ({rcpt_code}): {msg_str.strip()[:100]}",
                    }
                else:
                    return {
                        "email": clean_email,
                        "status": "INCONCLUSIVO",
                        "valido": None,
                        "mx_host": mx,
                        "smtp_code": rcpt_code,
                        "is_catch_all": False,
                        "detalhe": f"Resposta intermediária ({rcpt_code}): {msg_str.strip()[:100]}",
                    }

        except Exception as e:
            last_error = str(e)
            logger.debug("SMTP attempt on %s failed: %s", mx, e)

    return {
        "email": clean_email,
        "status": "ERRO_CONEXAO",
        "valido": None,
        "mx_host": target_mx,
        "smtp_code": None,
        "is_catch_all": False,
        "detalhe": f"Não foi possível conectar ao servidor MX ({last_error or 'timeout'}).",
    }


def verify_email_smtp(
    email: str,
    sender: str = DEFAULT_PROBE_SENDER,
    helo_host: str = DEFAULT_HELO_HOST,
    timeout: int = 7,
    use_remote_daemon: bool = True,
) -> Dict[str, Any]:
    """
    Executes a non-intrusive SMTP mailbox verification handshake.
    Delegates to host microservice if running in Docker, otherwise runs direct.
    """
    if use_remote_daemon:
        svc_url = get_verifier_service_url()
        # Avoid self-recursion if already in the service daemon
        if svc_url and os.getenv("IS_SMTP_DAEMON") != "true":
            try:
                payload = json.dumps({"email": email}).encode("utf-8")
                req = urllib.request.Request(
                    f"{svc_url}/verify",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=timeout) as res:
                    return json.loads(res.read().decode("utf-8"))
            except Exception as e:
                logger.debug("Delegation to verifier service %s failed: %s; falling back to direct", svc_url, e)

    return _verify_email_smtp_direct(email, sender, helo_host, timeout)


def _find_valid_executive_email_direct(
    full_name: str,
    domain: str,
    alternate_domain: Optional[str] = None,
) -> Dict[str, Any]:
    clean_name = unicodedata.normalize("NFKD", full_name).encode("ascii", "ignore").decode("utf-8").lower()
    parts = [p for p in re.sub(r"[^a-z\s]", "", clean_name).split() if len(p) > 1]
    if not parts:
        return {"email": None, "status": "NOME_INVALIDO", "valido": False}

    first = parts[0]
    last = parts[-1]

    domains_to_try = [domain]
    if alternate_domain and alternate_domain != domain:
        domains_to_try.append(alternate_domain)

    candidate_patterns = []
    for d in domains_to_try:
        candidate_patterns.append((f"{first}.{last}@{d}", "{primeiro_nome}.{sobrenome}@{dominio}"))
        candidate_patterns.append((f"{first[0]}{last}@{d}", "{p}{sobrenome}@{dominio}"))
        candidate_patterns.append((f"{first}@{d}", "{primeiro_nome}@{dominio}"))

    best_result = None

    for cand_email, pattern_str in candidate_patterns:
        res = verify_email_smtp(cand_email, use_remote_daemon=False)
        if res.get("valido") is True:
            res["padrao_identificado"] = pattern_str
            return res
        if not best_result:
            best_result = res
            best_result["padrao_identificado"] = pattern_str

    return best_result or {
        "email": f"{first}.{last}@{domain}",
        "status": "NAO_VERIFICADO",
        "valido": False,
        "padrao_identificado": "{primeiro_nome}.{sobrenome}@{dominio}",
    }


def find_valid_executive_email(
    full_name: str,
    domain: str,
    alternate_domain: Optional[str] = None,
    use_remote_daemon: bool = True,
) -> Dict[str, Any]:
    """
    Generates standard corporate email permutations for an executive and tests them via SMTP.
    Returns the first deliverable verified email.
    """
    if use_remote_daemon:
        svc_url = get_verifier_service_url()
        if svc_url and os.getenv("IS_SMTP_DAEMON") != "true":
            try:
                payload = json.dumps({
                    "name": full_name,
                    "domain": domain,
                    "alternate_domain": alternate_domain,
                }).encode("utf-8")
                req = urllib.request.Request(
                    f"{svc_url}/find-executive",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=20) as res:
                    return json.loads(res.read().decode("utf-8"))
            except Exception as e:
                logger.debug("Delegation to verifier service %s failed: %s; falling back to direct", svc_url, e)

    return _find_valid_executive_email_direct(full_name, domain, alternate_domain)


def filter_valid_emails(emails: List[str], max_to_verify: int = 10) -> List[str]:
    """
    Filters a list of email addresses, returning only those that pass:
    1. Basic RFC syntax validation
    2. Are not from personal/free providers (gmail, hotmail, etc.)
    3. For the first max_to_verify addresses: SMTP verification returning non-INVALIDO status

    Addresses that cannot be confirmed as definitively invalid (INCONCLUSIVO, ERRO_CONEXAO,
    CATCH_ALL, HEURISTICA) are kept — only explicit INVALIDO (5xx rejection) are removed.
    """
    PERSONAL_DOMAINS = {
        "gmail.com", "hotmail.com", "yahoo.com", "outlook.com", "live.com",
        "bol.com.br", "uol.com.br", "terra.com.br", "ig.com.br",
        "icloud.com", "me.com", "protonmail.com",
    }
    NOISE_PATTERNS = [
        "example.com", "wix.com", "domain.com", "seudominio.com",
        "noreply", "no-reply", "donotreply",
    ]

    valid = []
    verified_count = 0

    for em in emails:
        em = em.strip().lower()
        if not em or not validate_email_syntax(em):
            continue
        domain = em.split("@")[-1]
        if domain in PERSONAL_DOMAINS:
            continue
        if any(noise in em for noise in NOISE_PATTERNS):
            continue

        # SMTP verify the first max_to_verify unique-domain emails
        if verified_count < max_to_verify:
            try:
                result = verify_email_smtp(em)
                status = result.get("status", "")
                if status == "INVALIDO":
                    logger.debug("filter_valid_emails: dropped %s (INVALIDO)", em)
                    verified_count += 1
                    continue
                verified_count += 1
            except Exception as e:
                logger.debug("filter_valid_emails: could not verify %s: %s — keeping", em, e)

        valid.append(em)

    return valid


_PHONE_DIGITS_RE = re.compile(r"\d")
_PHONE_CLEAN_RE = re.compile(r"[\s\-\.\(\)]+")


def normalize_phone(raw: str) -> Optional[str]:
    """
    Normalizes a Brazilian phone string to (XX) XXXX-XXXX or (XX) 9XXXX-XXXX.
    Returns None for obviously invalid inputs.
    """
    if not raw:
        return None
    digits = "".join(_PHONE_DIGITS_RE.findall(str(raw)))
    # Strip country code 55
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]
    if len(digits) == 10:
        return f"({digits[:2]}) {digits[2:6]}-{digits[6:]}"
    if len(digits) == 11:
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"
    return None


def filter_valid_phones(phones: List[str]) -> List[str]:
    """
    Normalizes and deduplicates a list of Brazilian phone numbers.
    Removes malformed entries (not 10–11 digits after stripping non-digits).
    """
    seen: set = set()
    valid: List[str] = []
    for ph in phones:
        normalized = normalize_phone(ph)
        if normalized and normalized not in seen:
            # Basic sanity: DDD 11–99, not test numbers
            ddd = normalized[1:3]
            if ddd.isdigit() and 11 <= int(ddd) <= 99:
                seen.add(normalized)
                valid.append(normalized)
    return valid
