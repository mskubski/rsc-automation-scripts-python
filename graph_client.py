"""
Shared Microsoft Graph + SharePoint REST client.

Usage:
    from graph_client import graph_get, graph_post, graph_put, graph_paginate
    from graph_client import sp_get_request_digest, sp_rest_post
"""

import os
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from graph_auth import get_graph_token, get_sharepoint_token

load_dotenv()

_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


def _graph_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {get_graph_token()}",
    }


def _graph_url(path: str) -> str:
    return path if path.startswith("http") else f"{_GRAPH_BASE_URL}{path}"


def _raise_for_graph_error(resp: requests.Response) -> dict:
    resp.raise_for_status()
    data = resp.json() if resp.text else {}
    if "error" in data:
        raise RuntimeError(f"Graph API error: {data['error']}")
    return data


def graph_get(path: str, params: dict | None = None) -> dict:
    resp = requests.get(_graph_url(path), headers=_graph_headers(), params=params)
    return _raise_for_graph_error(resp)


def graph_post(path: str, json_body: dict) -> dict:
    resp = requests.post(_graph_url(path), headers=_graph_headers(), json=json_body)
    return _raise_for_graph_error(resp)


def graph_put(path: str, json_body: dict) -> dict:
    resp = requests.put(_graph_url(path), headers=_graph_headers(), json=json_body)
    return _raise_for_graph_error(resp)


def graph_paginate(path: str, params: dict | None = None) -> list:
    results = []
    data = graph_get(path, params=params)
    results.extend(data.get("value", []))
    next_link = data.get("@odata.nextLink")
    while next_link:
        data = graph_get(next_link)
        results.extend(data.get("value", []))
        next_link = data.get("@odata.nextLink")
    return results


def sp_tenant_hostname_from_site_url(site_url: str) -> str:
    return urlparse(site_url).netloc


def sp_get_request_digest(site_url: str) -> str:
    hostname = sp_tenant_hostname_from_site_url(site_url)
    headers = {
        "Authorization": f"Bearer {get_sharepoint_token(hostname)}",
        "Accept": "application/json;odata=verbose",
    }
    resp = requests.post(f"{site_url}/_api/contextinfo", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return data["d"]["GetContextWebInformation"]["FormDigestValue"]


def sp_rest_get(site_url: str, api_path: str) -> dict:
    hostname = sp_tenant_hostname_from_site_url(site_url)
    headers = {
        "Authorization": f"Bearer {get_sharepoint_token(hostname)}",
        "Accept": "application/json;odata=verbose",
    }
    resp = requests.get(f"{site_url}{api_path}", headers=headers)
    resp.raise_for_status()
    return resp.json()


def sp_rest_post(site_url: str, api_path: str, body: dict) -> dict:
    digest = sp_get_request_digest(site_url)
    hostname = sp_tenant_hostname_from_site_url(site_url)
    headers = {
        "Authorization": f"Bearer {get_sharepoint_token(hostname)}",
        "Accept": "application/json;odata=verbose",
        "Content-Type": "application/json;odata=verbose",
        "X-RequestDigest": digest,
    }
    resp = requests.post(f"{site_url}{api_path}", headers=headers, json=body)
    if not resp.ok:
        raise RuntimeError(f"SharePoint REST error ({resp.status_code}): {resp.text}")
    return resp.json() if resp.text else {}
