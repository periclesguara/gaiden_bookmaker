from __future__ import annotations

import socket
from urllib.parse import urlparse


DEFAULT_BASE_URL = "https://api.openai.com/v1"


def _normalize_base_url(raw: str | None) -> str:
    if not raw:
        return DEFAULT_BASE_URL
    base = raw.strip().rstrip("/")
    while base.endswith("/v1/v1"):
        base = base[:-3]
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def _host_from_base_url(base_url: str | None) -> str:
    parsed = urlparse(_normalize_base_url(base_url))
    if parsed.hostname:
        return parsed.hostname
    return "api.openai.com"


def assert_dns_reachability(base_url: str | None = None) -> None:
    host = _host_from_base_url(base_url)
    try:
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except Exception as exc:
        raise RuntimeError(f"DNS_FAIL {host}: {exc!r}") from exc


def assert_http_reachability(base_url: str | None = None, *, timeout: float = 10.0) -> None:
    import httpx

    base = _normalize_base_url(base_url)
    url = f"{base}/models"
    try:
        resp = httpx.head(url, timeout=timeout, follow_redirects=True)
    except Exception as exc:
        raise RuntimeError(f"HTTP_FAIL {url}: {exc!r}") from exc
    if resp.status_code not in {200, 401, 403}:
        raise RuntimeError(f"HTTP_FAIL {url}: status={resp.status_code}")


def preflight_openai(base_url: str | None = None) -> None:
    assert_dns_reachability(base_url)
    assert_http_reachability(base_url)
