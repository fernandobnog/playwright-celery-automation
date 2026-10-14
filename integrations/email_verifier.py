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
_domain_catch_all_cache: Dict[str, bool] = {}


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
                    clean_dom = domain.lower()
                    if clean_dom in _domain_catch_all_cache:
                        is_catch = _domain_catch_all_cache[clean_dom]
                    else:
                        rand_bogus = "probe_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10)) + f"@{domain}"
                        catch_code, _ = smtp.rcpt(rand_bogus)
                        is_catch = (catch_code == 250)
                        _domain_catch_all_cache[clean_dom] = is_catch

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


def verify_emails_batch(
    emails: List[str],
    timeout: int = 5,
    max_workers: int = 6,
) -> List[Dict[str, Any]]:
    """
    Validates a list of emails concurrently. Uses host microservice batch endpoint
    if running in Docker, otherwise uses local thread pool.
    """
    if not emails:
        return []

    clean_emails = [e.strip().lower() for e in emails if e and validate_email_syntax(str(e).strip().lower())]
    if not clean_emails:
        return []

    svc_url = get_verifier_service_url()
    if svc_url and os.getenv("IS_SMTP_DAEMON") != "true":
        try:
            payload = json.dumps({"emails": clean_emails}).encode("utf-8")
            req = urllib.request.Request(
                f"{svc_url}/verify-batch",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout * 3) as res:
                data = json.loads(res.read().decode("utf-8"))
                return data.get("results", [])
        except Exception as e:
            logger.debug("verify-batch via service %s failed: %s; falling back to thread pool", svc_url, e)

    import concurrent.futures
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(clean_emails), max_workers)) as executor:
        future_to_email = {executor.submit(verify_email_smtp, em): em for em in clean_emails}
        for future in concurrent.futures.as_completed(future_to_email):
            em = future_to_email[future]
            try:
                results.append(future.result())
            except Exception as e:
                results.append({
                    "email": em,
                    "status": "ERRO_CONEXAO",
                    "valido": None,
                    "detalhe": str(e),
                })
    return results


def generate_corporate_email_permutations(full_name: str, domain: str) -> List[Tuple[str, str]]:
    """
    Generates an exhaustive, prioritized combinatorial list of corporate email address permutations
    for a given individual's name and corporate domain.
    """
    clean_name = unicodedata.normalize("NFKD", full_name).encode("ascii", "ignore").decode("utf-8").lower()
    parts = [p for p in re.sub(r"[^a-z\s]", "", clean_name).split() if len(p) > 1]
    if not parts:
        return []

    first = parts[0]
    last = parts[-1]
    d = domain.lower().replace("http://", "").replace("https://", "").split("/")[0].replace("www.", "")

    patterns: List[Tuple[str, str]] = [
        (f"{first}.{last}@{d}", "{nome}.{sobrenome}@{dominio}"),
        (f"{first}{last}@{d}", "{nome}{sobrenome}@{dominio}"),
        (f"{first}@{d}", "{nome}@{dominio}"),
        (f"{first[0]}{last}@{d}", "{p}{sobrenome}@{dominio}"),
        (f"{first[0]}.{last}@{d}", "{p}.{sobrenome}@{dominio}"),
        (f"{first}_{last}@{d}", "{nome}_{sobrenome}@{dominio}"),
        (f"{last}.{first}@{d}", "{sobrenome}.{nome}@{dominio}"),
        (f"{last}{first}@{d}", "{sobrenome}{nome}@{dominio}"),
        (f"{last}@{d}", "{sobrenome}@{dominio}"),
        (f"{last[0]}{first}@{d}", "{s}{nome}@{dominio}"),
        (f"{last[0]}.{first}@{d}", "{s}.{nome}@{dominio}"),
        (f"{first}.{last[0]}@{d}", "{nome}.{s}@{dominio}"),
        (f"{first}{last[0]}@{d}", "{nome}{s}@{dominio}"),
    ]

    if len(parts) >= 3:
        second = parts[1]
        patterns.extend([
            (f"{first}.{second}@{d}", "{nome}.{segundo_nome}@{dominio}"),
            (f"{first}{second}@{d}", "{nome}{segundo_nome}@{dominio}"),
            (f"{first}.{second[0]}.{last}@{d}", "{nome}.{m_inicial}.{sobrenome}@{dominio}"),
            (f"{first[0]}{second[0]}{last}@{d}", "{p}{s}{sobrenome}@{dominio}"),
            (f"{first}.{second}.{last}@{d}", "{nome}.{segundo_nome}.{sobrenome}@{dominio}"),
            (f"{first}_{second}_{last}@{d}", "{nome}_{segundo_nome}_{sobrenome}@{dominio}"),
        ])

    seen = set()
    unique: List[Tuple[str, str]] = []
    for em, pat in patterns:
        if em not in seen:
            seen.add(em)
            unique.append((em, pat))
    return unique


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

    candidate_patterns: List[Tuple[str, str]] = []
    # Test top 3 most common corporate permutations on primary domain
    primary_perms = generate_corporate_email_permutations(full_name, domain)
    candidate_patterns.extend(primary_perms[:3])
    if alternate_domain and alternate_domain != domain:
        alt_perms = generate_corporate_email_permutations(full_name, alternate_domain)
        candidate_patterns.extend(alt_perms[:1])

    best_result = None
    all_tested: List[Dict[str, Any]] = []

    for cand_email, pattern_str in candidate_patterns:
        try:
            res = verify_email_smtp(cand_email, timeout=3, use_remote_daemon=False)
            audit_entry = {
                "email": cand_email,
                "valido": res.get("valido"),
                "status": res.get("status", "INCONCLUSIVO"),
                "is_catch_all": res.get("is_catch_all", False),
                "mx_host": res.get("mx_host"),
                "smtp_code": res.get("smtp_code"),
                "detalhe": res.get("detalhe", "Verificação SMTP concluída"),
                "padrao_identificado": pattern_str,
            }
            all_tested.append(audit_entry)

            if res.get("valido") is True:
                res["padrao_identificado"] = pattern_str
                res["email"] = cand_email
                res["emails_validados"] = all_tested
                return res
            # Keep catch-all or inconclusive if better than pure invalid
            if res.get("status") in ("CATCH_ALL", "INCONCLUSIVO", "ERRO_CONEXAO"):
                if not best_result or best_result.get("status") == "INVALIDO":
                    best_result = res
                    best_result["padrao_identificado"] = pattern_str
                    best_result["email"] = cand_email
            elif not best_result:
                best_result = res
                best_result["padrao_identificado"] = pattern_str
                best_result["email"] = cand_email
        except Exception as e:
            all_tested.append({
                "email": cand_email,
                "valido": None,
                "status": "ERRO_CONEXAO",
                "is_catch_all": False,
                "mx_host": None,
                "smtp_code": None,
                "detalhe": f"Falha de conexão: {e}",
                "padrao_identificado": pattern_str,
            })

    # If all explicit tests were rejected or failed, provide the standard corporate pattern
    # marked as PADRAO_CORPORATIVO_ESTIMADO rather than leaving it empty
    default_email = f"{first}.{last}@{domain}"
    if best_result and best_result.get("status") in ("CATCH_ALL", "INCONCLUSIVO", "ERRO_CONEXAO"):
        best_result["emails_validados"] = all_tested
        return best_result

    return {
        "email": default_email,
        "status": "PADRAO_CORPORATIVO_ESTIMADO",
        "valido": None,
        "padrao_identificado": "{primeiro_nome}.{sobrenome}@{dominio}",
        "detalhe": "E-mail corporativo estruturado a partir da taxonomia nominal da organização.",
        "emails_validados": all_tested or [{
            "email": default_email,
            "valido": None,
            "status": "PADRAO_CORPORATIVO_ESTIMADO",
            "is_catch_all": False,
            "mx_host": None,
            "smtp_code": None,
            "detalhe": "Padrão corporativo estimado.",
        }],
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


RAMAL_REGEX = re.compile(
    r"(?:ramal|ramais|ext(?:ens[aã]o)?|r\.|r:|r/)(?:\s+(?:de|do|da|dos|das|[a-zA-Záéíóúãõç]{2,15})){0,3}\s*[:\-]?\s*([0-9]{1,6}(?:[,\s/e]+[0-9]{1,6})*)",
    re.IGNORECASE,
)
_PHONE_DIGITS_RE = re.compile(r"\d")
_PHONE_CLEAN_RE = re.compile(r"[\s\-\.\(\)]+")


def normalize_phone(raw: str) -> Optional[str]:
    """
    Normalizes a Brazilian phone string to (XX) XXXX-XXXX or (XX) 9XXXX-XXXX.
    Gracefully strips extensions/ramais before extracting phone digits.
    Returns None for obviously invalid inputs.
    """
    if not raw:
        return None
    # Strip ramal / extension suffix before extracting digits
    clean_raw = re.sub(
        r"(?:ramal|ramais|ext(?:ens[aã]o)?|r\.|r:|r/)\s*[0-9\s,/-]+",
        "",
        str(raw),
        flags=re.IGNORECASE,
    )
    digits = "".join(_PHONE_DIGITS_RE.findall(clean_raw))
    # Strip country code 55
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]
    if len(digits) == 10:
        return f"({digits[:2]}) {digits[2:6]}-{digits[6:]}"
    if len(digits) == 11:
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"
    return None


def parse_phone_and_ramal(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extracts normalized phone and telephone extension (ramal) from a string.
    Example: '(19) 3404-5000 Ramal 230' -> ('(19) 3404-5000', '230')
    """
    if not raw:
        return None, None
    raw_str = str(raw)
    ramal_m = RAMAL_REGEX.search(raw_str)
    ramal = ramal_m.group(1).strip() if ramal_m else None
    norm_ph = normalize_phone(raw_str)
    return norm_ph, ramal


def extract_phones_and_ramais(text: str) -> Tuple[List[str], List[str]]:
    """
    Extracts all Brazilian phone numbers and associated telephone extensions (ramais)
    from freeform text.
    """
    if not text:
        return [], []
    phones: List[str] = []
    ramais: List[str] = []
    seen_ph: set = set()
    seen_ram: set = set()

    # Match phones with optional extension suffix
    pattern = re.compile(
        r"(?:(?:\+55\s*)?\(?\d{2}\)?\s*(?:9\s*)?\d{4}[-\s.]?\d{4}(?:\s*(?:ramal|ramais|ext(?:ens[aã]o)?|r\.|r:|r/)\s*[0-9\s,/-]+)?)",
        re.IGNORECASE,
    )
    for m in pattern.findall(text):
        norm_ph, r_str = parse_phone_and_ramal(m)
        if norm_ph and norm_ph not in seen_ph:
            seen_ph.add(norm_ph)
            phones.append(norm_ph)
        if r_str:
            for piece in re.findall(r"\d{1,6}", r_str):
                if piece not in seen_ram:
                    seen_ram.add(piece)
                    ramais.append(piece)

    for r_match in RAMAL_REGEX.finditer(text):
        r_val = r_match.group(1)
        for piece in re.findall(r"\d{1,6}", r_val):
            if piece not in seen_ram:
                seen_ram.add(piece)
                ramais.append(piece)

    return phones, ramais


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
