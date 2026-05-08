"""
Shared authentication helper for RSC scripts.

Usage:
    from rsc_auth import get_token
    token = get_token()

Tokens are cached in .rsc_token_cache (same directory as this file)
and reused until they are within TOKEN_BUFFER_SECONDS of expiry.
This avoids exhausting Rubrik's 10-token-per-service-account limit.
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
_CACHE_FILE = Path(__file__).parent / ".rsc_token_cache"


def _decode_jwt_exp(token: str) -> int:
    payload = token.split(".")[1]
    # JWT uses base64url — restore standard base64 padding
    padding = 4 - len(payload) % 4
    payload += "=" * (padding % 4)
    decoded = base64.urlsafe_b64decode(payload)
    return json.loads(decoded).get("exp", 0)


def get_token() -> str:
    if _CACHE_FILE.exists():
        cached = _CACHE_FILE.read_text().strip()
        if cached:
            exp = _decode_jwt_exp(cached)
            remaining = exp - int(time.time())
            if remaining > TOKEN_BUFFER_SECONDS:
                print(f"-> Using cached token (expires in {remaining}s).")
                return cached
            print("-> Cached token expired or within buffer window. Requesting new token...")

    token_uri = os.environ["RSC_TOKEN_URI"]
    client_id = os.environ["RSC_CLIENT_ID"]
    client_secret = os.environ["RSC_CLIENT_SECRET"]

    resp = requests.post(
        token_uri,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")

    if not token:
        raise RuntimeError(f"Failed to obtain access token. Response: {resp.text}")

    _CACHE_FILE.write_text(token)
    _CACHE_FILE.chmod(0o600)

    exp = _decode_jwt_exp(token)
    remaining = exp - int(time.time())
    print(f"-> New token obtained (expires in {remaining}s, cached to .rsc_token_cache).")
    return token
