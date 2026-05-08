"""
Shared GraphQL client for RSC scripts.

Usage:
    from rsc_client import gql, gql_vars, gql_vars_raw
"""

import os

import requests
from dotenv import load_dotenv

from rsc_auth import get_token

load_dotenv()


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {get_token()}",
    }


def _url() -> str:
    return f"https://{os.environ['RSC_FQDN']}/api/graphql"


def gql(query: str) -> dict:
    resp = requests.post(_url(), headers=_headers(), json={"query": query})
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL error: {data['errors']}")
    return data


def gql_vars(query: str, variables: dict) -> dict:
    resp = requests.post(_url(), headers=_headers(), json={"query": query, "variables": variables})
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL error: {data['errors']}")
    return data


def gql_vars_raw(query: str, variables: dict) -> dict:
    """No error raise — use for retry logic."""
    resp = requests.post(_url(), headers=_headers(), json={"query": query, "variables": variables})
    resp.raise_for_status()
    return resp.json()
