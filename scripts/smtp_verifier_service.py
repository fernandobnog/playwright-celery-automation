#!/usr/bin/env python3
"""
SMTP Email Deliverability Verifier Daemon.
Runs as a local microservice (default port 9095) on the host system to provide
zero-bounce mailbox validation via SMTP handshakes without Docker NAT or bridge limitations.
"""

import json
import logging
import os
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Ensure integrations module can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["IS_SMTP_DAEMON"] = "true"

from integrations.email_verifier import (
    verify_email_smtp,
    find_valid_executive_email,
    validate_email_syntax,
    get_mx_hosts,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [SMTP-Daemon] %(message)s",
)
logger = logging.getLogger("smtp_daemon")

HOST = os.getenv("SMTP_VERIFIER_HOST", "0.0.0.0")
PORT = int(os.getenv("SMTP_VERIFIER_PORT", "9095"))


class SMTPVerifierHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Override to use standard logger
        logger.info("%s - - [%s] %s", self.client_address[0], self.log_date_time_string(), format % args)

    def _send_json(self, status_code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        if path in ("", "/health"):
            self._send_json(200, {"status": "ok", "service": "smtp-deliverability-verifier", "version": "1.0.0"})
            return

        if path == "/verify":
            email = params.get("email", [None])[0]
            if not email:
                self._send_json(400, {"error": "Parametro 'email' obrigatorio na query string."})
                return
            t0 = time.time()
            result = verify_email_smtp(email)
            result["execution_time_seconds"] = round(time.time() - t0, 3)
            self._send_json(200, result)
            return

        self._send_json(404, {"error": f"Rota '{path}' nao encontrada."})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        content_length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            payload = json.loads(body_bytes.decode("utf-8") or "{}")
        except Exception as e:
            self._send_json(400, {"error": f"JSON invalido: {e}"})
            return

        if path == "/verify":
            email = payload.get("email")
            if not email:
                self._send_json(400, {"error": "Campo 'email' obrigatorio no body JSON."})
                return
            t0 = time.time()
            result = verify_email_smtp(email)
            result["execution_time_seconds"] = round(time.time() - t0, 3)
            self._send_json(200, result)
            return

        if path == "/find-executive":
            name = payload.get("name") or payload.get("nome")
            domain = payload.get("domain") or payload.get("dominio")
            alt_domain = payload.get("alternate_domain") or payload.get("dominio_alternativo")
            if not name or not domain:
                self._send_json(400, {"error": "Campos 'name' e 'domain' obrigatorios no body JSON."})
                return
            t0 = time.time()
            result = find_valid_executive_email(name, domain, alt_domain)
            result["execution_time_seconds"] = round(time.time() - t0, 3)
            self._send_json(200, result)
            return

        self._send_json(404, {"error": f"Rota '{path}' nao encontrada."})


def run_daemon():
    server_address = (HOST, PORT)
    httpd = HTTPServer(server_address, SMTPVerifierHandler)
    logger.info("SMTP Verifier Daemon listening on %s:%d", HOST, PORT)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping daemon...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run_daemon()
