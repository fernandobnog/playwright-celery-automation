"""
Core Security Module for OmniFlow.
Implements Zero-Trust Container-to-Container Security:
1. Internal Pre-Shared API Key Authentication (X-API-Key, Bearer token, ?api_key=).
2. Strict Anti-SSRF (Server-Side Request Forgery) Protection.
3. Internal Docker Network / CIDR IP filtering middleware.
4. OWASP Security headers & server identity masking.
5. Request payload size limit (DoS mitigation).
"""

import ipaddress
import logging
import secrets
import socket
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, APIKeyQuery, HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from core.config import settings

logger = logging.getLogger(__name__)

# Authentication Schemes for Swagger UI & Header Extraction
api_key_header_scheme = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="Internal container pre-shared API Key (Header: X-API-Key)",
)
bearer_token_scheme = HTTPBearer(
    auto_error=False,
    description="Internal container pre-shared Bearer Token (Header: Authorization: Bearer <key>)",
)
api_key_query_scheme = APIKeyQuery(
    name="api_key",
    auto_error=False,
    description="Internal container API Key passed via URL Query Parameter (?api_key=<key>)",
)

# Hostnames strictly blocked for web scraping to prevent internal pivot / SSRF
BLOCKED_SCRAPE_HOSTNAMES = {
    "localhost",
    "ip6-localhost",
    "ip6-loopback",
    "redis",
    "omniflow_redis",
    "omniflow_api",
    "omniflow_beat",
    "omniflow_flower",
    "omniflow_worker_1",
    "omniflow_worker_2",
    "worker-1",
    "worker-2",
    "flower",
    "api",
    "n8n",
    "evolution-api",
    "twenty_server",
    "site-backend",
    "metadata.google.internal",
}


def verify_internal_api_key(
    request: Request,
    header_key: Optional[str] = Security(api_key_header_scheme),
    bearer_auth: Optional[HTTPAuthorizationCredentials] = Security(bearer_token_scheme),
    query_key: Optional[str] = Security(api_key_query_scheme),
) -> str:
    """
    Validates the internal pre-shared API key between containers.
    Accepts:
      1. Header 'X-API-Key: <key>'
      2. Header 'Authorization: Bearer <key>'
      3. Query parameter '?api_key=<key>'
    """
    if not settings.REQUIRE_API_KEY:
        return "anonymous"

    if not settings.INTERNAL_API_KEY:
        logger.warning("REQUIRE_API_KEY is True but INTERNAL_API_KEY is not configured in environment.")
        return "unconfigured"

    token = header_key or (bearer_auth.credentials if bearer_auth else None) or query_key

    # Also check request query params directly in case framework didn't inject
    if not token and "api_key" in request.query_params:
        token = request.query_params["api_key"]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Missing internal API key. Provide via 'X-API-Key' header, 'Bearer' token, or '?api_key=' parameter.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not secrets.compare_digest(token, settings.INTERNAL_API_KEY):
        logger.warning("Unauthorized access attempt with invalid API key from client: %s", request.client.host if request.client else "unknown")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid internal API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token


def validate_url_for_ssrf(url: str, allow_internal_containers: bool = False) -> None:
    """
    Validates that a URL target does not attempt SSRF (Server-Side Request Forgery).
    Blocks access to:
    - Loopback addresses (127.0.0.1, ::1)
    - Link-local and Cloud metadata (169.254.0.0/16, AWS/GCP metadata)
    - Private RFC1918 subnets (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
    - Multicast, reserved, and unspecified addresses
    - Local Docker internal container hostnames
    - Non-HTTP(S) schemes (file://, gopher://, ftp://, dict://)
    """
    if not settings.ENABLE_SSRF_PROTECTION:
        return

    if not url:
        raise ValueError("SSRF Protection: Empty URL provided.")

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"SSRF Protection: Forbidden protocol scheme '{scheme}'. Only http and https are allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("SSRF Protection: URL must contain a valid domain or hostname.")

    hostname = hostname.lower().strip()

    # 1. Reject well-known local/internal hostnames
    if not allow_internal_containers:
        if (
            hostname in BLOCKED_SCRAPE_HOSTNAMES
            or hostname.endswith(".local")
            or hostname.endswith(".internal")
            or hostname.endswith(".localhost")
        ):
            raise ValueError(f"SSRF Protection: Access to internal/local hostname '{hostname}' is blocked.")

    # 2. Check if hostname is an explicit raw IP address
    try:
        raw_ip = ipaddress.ip_address(hostname)
        _check_ip_safety(raw_ip, allow_private=allow_internal_containers)
        return
    except ValueError:
        # Not a raw IP literal; proceed to DNS resolution
        pass

    # 3. Resolve hostname via DNS and check all resolved IP addresses
    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise ValueError(f"SSRF Protection: DNS resolution failed for host '{hostname}': {e}")

    if not addr_info:
        raise ValueError(f"SSRF Protection: No DNS records found for host '{hostname}'.")

    for entry in addr_info:
        ip_str = entry[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
            _check_ip_safety(ip, allow_private=allow_internal_containers)
        except ValueError as e:
            raise e


def _check_ip_safety(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, allow_private: bool = False) -> None:
    """Helper verifying that an IP address does not belong to forbidden/internal ranges."""
    if ip.is_loopback:
        raise ValueError(f"SSRF Protection: Target IP {ip} is a loopback address and cannot be accessed.")
    if ip.is_link_local:
        raise ValueError(f"SSRF Protection: Target IP {ip} is a link-local/cloud metadata address (169.254.x.x) and cannot be accessed.")
    if not allow_private and ip.is_private:
        raise ValueError(f"SSRF Protection: Target IP {ip} is a private network address and cannot be accessed.")
    if ip.is_reserved:
        raise ValueError(f"SSRF Protection: Target IP {ip} is a reserved address and cannot be accessed.")
    if ip.is_multicast:
        raise ValueError(f"SSRF Protection: Target IP {ip} is a multicast address and cannot be accessed.")
    if ip.is_unspecified:
        raise ValueError(f"SSRF Protection: Target IP {ip} is an unspecified address and cannot be accessed.")


class InternalNetworkMiddleware(BaseHTTPMiddleware):
    """
    Enforces that incoming HTTP requests to the API Gateway originate strictly
    from internal Docker networks, localhost loopback, or authorized private CIDRs.
    Any direct access from external public IPs is immediately rejected with 403 Forbidden.
    """

    def __init__(self, app, allowed_cidrs: Optional[List[str]] = None, enabled: bool = True):
        super().__init__(app)
        self.enabled = enabled
        cidrs = allowed_cidrs or [
            "127.0.0.0/8",      # Loopback IPv4
            "::1/128",          # Loopback IPv6
            "10.0.0.0/8",       # RFC 1918 Class A
            "172.16.0.0/12",    # RFC 1918 Class B (Standard Docker Networks)
            "192.168.0.0/16",   # RFC 1918 Class C
        ]
        self.allowed_networks = [ipaddress.ip_network(net) for net in cidrs]

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)

        # Allow token-authenticated public approval callback
        if request.url.path.startswith("/api/v1/leads/approve"):
            return await call_next(request)

        client_host = request.client.host if request.client else None
        if client_host in ("testclient", "localhost"):
            return await call_next(request)

        if not client_host:
            logger.warning("Rejected request with undetermined client host.")
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Forbidden: Client host could not be determined."},
            )

        try:
            client_ip = ipaddress.ip_address(client_host)
            is_allowed = any(client_ip in net for net in self.allowed_networks)
            if not is_allowed:
                logger.warning("Blocked untrusted non-internal client IP: %s", client_host)
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Forbidden: Access restricted strictly to internal container network."},
                )
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Forbidden: Invalid client IP address format."},
            )

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Applies defensive OWASP security headers to all responses and masks server identity.
    """

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://static.cloudflareinsights.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https://cloudflareinsights.com; "
            "frame-ancestors 'none';"
        )
        response.headers["Server"] = "OmniFlow-Gateway"
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Prevents memory exhaustion and Denial of Service by enforcing maximum request size limits.
    """

    def __init__(self, app, max_size_bytes: int = 5 * 1024 * 1024):
        super().__init__(app)
        self.max_size_bytes = max_size_bytes

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.max_size_bytes:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={"detail": f"Payload too large. Maximum allowed size is {self.max_size_bytes} bytes."},
                    )
            except ValueError:
                pass
        return await call_next(request)


# ==============================================================================
# One-Click Approval Token Helpers (Human-in-the-Loop)
# ==============================================================================
def create_approval_token(phone: str, first_name: str, expires_in_seconds: int = 172800) -> str:
    """
    Generates a secure HMAC-signed token for one-click email/WhatsApp approval.
    Defaults to 48 hours validity.
    """
    import base64
    import hashlib
    import hmac
    import json
    import time

    payload = {
        "phone": phone,
        "first_name": first_name,
        "exp": int(time.time()) + expires_in_seconds,
    }
    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_payload).decode("utf-8").rstrip("=")

    secret = (settings.LEAD_APPROVAL_SECRET or "default_secret").encode("utf-8")
    signature = hmac.new(secret, b64_payload.encode("utf-8"), hashlib.sha256).hexdigest()

    return f"{b64_payload}.{signature}"


def verify_approval_token(token: str) -> Optional[dict]:
    """
    Validates the token signature and expiration. Returns payload dict or None.
    """
    import base64
    import hashlib
    import hmac
    import json
    import time

    if not token or "." not in token:
        return None

    try:
        b64_payload, signature = token.split(".", 1)
        secret = (settings.LEAD_APPROVAL_SECRET or "default_secret").encode("utf-8")
        expected_sig = hmac.new(secret, b64_payload.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(signature, expected_sig):
            return None

        # Add base64 padding
        pad = len(b64_payload) % 4
        if pad:
            b64_payload += "=" * (4 - pad)

        raw_payload = base64.urlsafe_b64decode(b64_payload.encode("utf-8")).decode("utf-8")
        payload = json.loads(raw_payload)

        if payload.get("exp", 0) < time.time():
            return None

        return payload
    except Exception as e:
        logger.warning("Failed to decode or verify approval token: %s", e)
        return None


def create_editorial_action_token(
    pauta_id: int,
    pauta_titulo: str,
    categoria: str,
    target_format: str = "both",
    angulo_editorial: Optional[str] = None,
    curation_date: Optional[str] = None,
    expires_in_seconds: int = 172800,
) -> str:
    """
    Generates a secure HMAC-signed token for selecting an editorial topic from email.
    Defaults to 48 hours validity.
    """
    import base64
    from datetime import datetime
    import hashlib
    import hmac
    import json
    import time

    payload = {
        "pauta_id": pauta_id,
        "pauta_titulo": pauta_titulo,
        "categoria": categoria,
        "target_format": target_format,
        "curation_date": curation_date or datetime.now().strftime("%Y%m%d"),
        "exp": int(time.time()) + expires_in_seconds,
    }
    if angulo_editorial:
        payload["angulo_editorial"] = angulo_editorial

    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_payload).decode("utf-8").rstrip("=")

    secret = (settings.LEAD_APPROVAL_SECRET or "default_secret").encode("utf-8")
    signature = hmac.new(secret, b64_payload.encode("utf-8"), hashlib.sha256).hexdigest()

    return f"{b64_payload}.{signature}"


def verify_editorial_action_token(token: str) -> Optional[dict]:
    """
    Validates the editorial action token signature and expiration.
    Returns payload dict or None.
    """
    import base64
    import hashlib
    import hmac
    import json
    import time

    if not token or "." not in token:
        return None

    try:
        b64_payload, signature = token.split(".", 1)
        secret = (settings.LEAD_APPROVAL_SECRET or "default_secret").encode("utf-8")
        expected_sig = hmac.new(secret, b64_payload.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(signature, expected_sig):
            return None

        # Add base64 padding
        pad = len(b64_payload) % 4
        if pad:
            b64_payload += "=" * (4 - pad)

        raw_payload = base64.urlsafe_b64decode(b64_payload.encode("utf-8")).decode("utf-8")
        payload = json.loads(raw_payload)

        if payload.get("exp", 0) < time.time():
            return None

        return payload
    except Exception as e:
        logger.warning("Failed to decode or verify editorial action token: %s", e)
        return None
