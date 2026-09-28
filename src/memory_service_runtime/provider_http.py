"""HTTP calls to model/embedding providers that carry a service credential.

Shared by the Clark façade's semantic search and the narrative API. Failures
raise without the provider's response text: upstreams sometimes echo headers.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx

MAX_RESPONSE_BYTES = 2_000_000


def checked_base_url(value: str) -> str:
    """Accept https, or http to this host only; reject embedded credentials."""
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("provider base URL is not an allowed endpoint")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("provider base URL must use https")
    return value


def post_json(url: str, key: str, payload: dict, timeout: float) -> dict:
    # Never follow a redirect with service credentials, never take a proxy from
    # the environment, and never buffer an unbounded response.
    with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
        with client.stream("POST", url, headers={"Authorization": f"Bearer {key}"}, json=payload) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise ValueError("provider response is too large")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("provider response must be an object")
    return result
