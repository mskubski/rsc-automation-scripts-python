"""
Shared authentication helper for Microsoft Graph / SharePoint scripts.

Independent of the RSC credentials (rsc_auth.py) -- this targets a Microsoft
Entra ID app registration (client credentials flow), not the Rubrik Security
Cloud tenant.

Usage:
    from graph_auth import get_graph_token, get_sharepoint_token

    token = get_graph_token()
    sp_token = get_sharepoint_token("contoso.sharepoint.com")

Tokens are cached (one file per resource, since Graph and SharePoint tokens
are issued for different audiences) and reused until they are within
TOKEN_BUFFER_SECONDS of expiry.
"""

import base64
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN_BUFFER_SECONDS = int(os.environ.get("TOKEN_BUFFER_SECONDS", 300))
_GRAPH_CACHE_FILE = Path(__file__).parent / ".graph_token_cache"
_SP_CACHE_FILE = Path(__file__).parent / ".graph_sp_token_cache"


def _decode_jwt_exp(token: str) -> int:
    payload = token.split(".")[1]
    # JWT uses base64url — restore standard base64 padding
    padding = 4 - len(payload) % 4
    payload += "=" * (padding % 4)
    decoded = base64.urlsafe_b64decode(payload)
    return json.loads(decoded).get("exp", 0)


def _cached_token(cache_file: Path) -> str | None:
    if not cache_file.exists():
        return None
    cached = cache_file.read_text().strip()
    if not cached:
        return None
    exp = _decode_jwt_exp(cached)
    remaining = exp - int(time.time())
    if remaining > TOKEN_BUFFER_SECONDS:
        print(f"-> Using cached token from {cache_file.name} (expires in {remaining}s).")
        return cached
    print(f"-> Cached token in {cache_file.name} expired or within buffer window. Requesting new token...")
    return None


def _request_client_credentials_token(scope: str) -> str:
    tenant_id = os.environ["GRAPH_TENANT_ID"]
    client_id = os.environ["GRAPH_CLIENT_ID"]
    client_secret = os.environ["GRAPH_CLIENT_SECRET"]

    resp = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
            "grant_type": "client_credentials",
        },
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")

    if not token:
        raise RuntimeError(f"Failed to obtain access token for scope {scope!r}. Response: {resp.text}")

    return token


def _get_cached_or_new_token(cache_file: Path, scope: str) -> str:
    cached = _cached_token(cache_file)
    if cached:
        return cached

    token = _request_client_credentials_token(scope)
    cache_file.write_text(token)
    cache_file.chmod(0o600)

    exp = _decode_jwt_exp(token)
    remaining = exp - int(time.time())
    print(f"-> New token obtained (expires in {remaining}s, cached to {cache_file.name}).")
    return token


def get_graph_token() -> str:
    """Token for Microsoft Graph (https://graph.microsoft.com)."""
    return _get_cached_or_new_token(_GRAPH_CACHE_FILE, "https://graph.microsoft.com/.default")


def get_sharepoint_token(sp_tenant_hostname: str) -> str:
    """Token for the classic SharePoint REST API (https://{tenant}.sharepoint.com)."""
    return _get_cached_or_new_token(_SP_CACHE_FILE, f"https://{sp_tenant_hostname}/.default")
