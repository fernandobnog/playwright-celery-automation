#!/usr/bin/env python3
"""
Google Workspace & Cloud OAuth2 / Service Account CLI Setup Tool.
Generates multi-scope authorization URLs, exchanges codes for permanent refresh tokens,
validates Service Accounts, and performs connectivity tests.
"""

import argparse
import os
from pathlib import Path
import sys
from typing import Optional
import urllib.parse

# Ensure repository root is on sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import requests
from integrations.google import ALL_COMBINED_SCOPES, GoogleHub
from core.config import settings


def generate_auth_url(client_id: str, redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob") -> str:
    """
    Builds the Google OAuth 2.0 authorization URL requesting offline access.
    """
    scopes_str = " ".join(ALL_COMBINED_SCOPES)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes_str,
        "access_type": "offline",
        "prompt": "consent",
    }
    base_url = "https://accounts.google.com/o/oauth2/v2/auth"
    return f"{base_url}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob",
) -> dict:
    """
    Exchanges an authorization code for access and refresh tokens.
    """
    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "code": code.strip(),
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    response = requests.post(token_url, data=payload, timeout=30)
    if not response.ok:
        print(f"\n❌ Erro ao trocar código por tokens: {response.status_code}")
        print(response.text)
        sys.exit(1)
    return response.json()


def update_env_file(refresh_token: str, env_path: Optional[str] = None):
    """
    Updates or inserts GOOGLE_REFRESH_TOKEN into .env file.
    """
    target = Path(env_path or REPO_ROOT / ".env")
    if not target.exists():
        print(f"⚠️ Arquivo {target} não encontrado. Não foi possível atualizar automaticamente.")
        return

    content = target.read_text(encoding="utf-8")
    key = "GOOGLE_REFRESH_TOKEN"

    if key in content:
        lines = []
        for line in content.splitlines():
            if line.startswith(f"{key}="):
                lines.append(f"{key}={refresh_token}")
            else:
                lines.append(line)
        new_content = "\n".join(lines) + "\n"
    else:
        new_content = content.rstrip() + f"\n\n# Google Unified OAuth Refresh Token\n{key}={refresh_token}\n"

    target.write_text(new_content, encoding="utf-8")
    print(f"✅ Variável {key} atualizada com sucesso em: {target}")


def test_connections():
    """
    Performs quick healthchecks on configured Google APIs.
    """
    print("=" * 60)
    print("🔍 Testando conectividade das APIs Google...")
    print("=" * 60)

    hub = GoogleHub()

    # 1. Test Sheets
    try:
        creds = hub.auth.get_credentials("sheets")
        print("✅ [Google Sheets] Credenciais resolvidas com sucesso.")
    except Exception as e:
        print(f"❌ [Google Sheets] Falha ao resolver credenciais: {e}")

    # 2. Test Gmail
    try:
        creds = hub.auth.get_credentials("gmail")
        print("✅ [Gmail API] Credenciais resolvidas com sucesso.")
    except Exception as e:
        print(f"❌ [Gmail API] Falha ao resolver credenciais: {e}")

    # 3. Test Calendar
    try:
        creds = hub.auth.get_credentials("calendar")
        print("✅ [Google Calendar] Credenciais resolvidas com sucesso.")
    except Exception as e:
        print(f"❌ [Google Calendar] Falha ao resolver credenciais: {e}")

    # 4. Test Docs & Drive
    try:
        creds_docs = hub.auth.get_credentials("docs")
        creds_drive = hub.auth.get_credentials("drive")
        print("✅ [Google Docs & Drive] Credenciais resolvidas com sucesso.")
    except Exception as e:
        print(f"❌ [Google Docs & Drive] Falha ao resolver credenciais: {e}")

    # 5. Check Service Account
    sa_creds = hub.auth.get_service_account_credentials()
    if sa_creds:
        print(f"🤖 [Service Account] Ativa e carregada: {sa_creds.service_account_email}")
    else:
        print("ℹ️ [Service Account] Nenhuma Service Account configurada (utilizando OAuth 2.0).")


def main():
    parser = argparse.ArgumentParser(description="Google APIs Auth Management CLI")
    parser.add_argument("--generate-oauth", action="store_true", help="Gera URL de autorização OAuth 2.0")
    parser.add_argument("--exchange-code", type=str, help="Código de autorização recebido após consentimento")
    parser.add_argument("--test-connection", action="store_true", help="Executa healthcheck das APIs configuradas")
    parser.add_argument("--client-id", type=str, help="Google Client ID (default lê do .env)")
    parser.add_argument("--client-secret", type=str, help="Google Client Secret (default lê do .env)")

    args = parser.parse_args()

    client_id = args.client_id or settings.GOOGLE_CLIENT_ID
    client_secret = args.client_secret or settings.GOOGLE_CLIENT_SECRET

    if args.test_connection:
        test_connections()
        return

    if args.exchange_code:
        if not client_id or not client_secret:
            print("❌ Erro: client_id e client_secret são obrigatórios.")
            sys.exit(1)
        tokens = exchange_code_for_tokens(args.exchange_code, client_id, client_secret)
        refresh_token = tokens.get("refresh_token")
        if refresh_token:
            print("\n🎉 Novo Refresh Token permanente obtido com sucesso!")
            print(f"🔑 Token: {refresh_token}")
            update_env_file(refresh_token)
        else:
            print("\n⚠️ O Google não retornou um refresh_token novo (o acesso já foi concedido anteriormente).")
            print("Para forçar a emissão de um novo token, certifique-se de que prompt=consent foi utilizado.")
        return

    if args.generate_oauth or len(sys.argv) == 1:
        if not client_id:
            print("❌ GOOGLE_CLIENT_ID não encontrado no .env nem passado via --client-id.")
            sys.exit(1)
        url = generate_auth_url(client_id)
        print("=" * 70)
        print("🔑 GOOGLE OAUTH 2.0 - GERADOR DE TOKEN MULTI-ESCOPO")
        print("=" * 70)
        print("\n1. Abra o link abaixo no seu navegador (logado na sua conta Google):")
        print(f"\n{url}\n")
        print("2. Autorize todas as permissões (Gmail, Calendar, Sheets, Docs, Drive).")
        print("3. Copie o código de autorização exibido na tela final.")
        print("\n4. Em seguida, execute:")
        print("   python scripts/google_auth_cli.py --exchange-code <SEU_CODIGO_AQUI>")
        print("=" * 70)


if __name__ == "__main__":
    main()
