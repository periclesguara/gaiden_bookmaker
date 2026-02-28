from __future__ import annotations

import socket
from typing import Any
from urllib.parse import urlparse


def _host_from_base_url(base_url: str | None) -> str:
    if not base_url:
        return "api.openai.com"
    parsed = urlparse(base_url)
    if parsed.hostname:
        return parsed.hostname
    return "api.openai.com"


def assert_dns_reachability(base_url: str | None = None) -> dict[str, Any]:
    host = _host_from_base_url(base_url)
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except Exception as exc:
        raise RuntimeError(f"DNS_FAIL for {host}: {exc!r}") from exc
    return {"host": host, "addr": infos[0][4][0]}


def assert_http_reachability(base_url: str | None = None, *, timeout: float = 10.0) -> dict[str, Any]:
    import httpx

    host = _host_from_base_url(base_url)
    base = base_url or "https://api.openai.com/v1"
    if not base.endswith("/v1"):
        base = base.rstrip("/") + "/v1"
    url = f"{base}/models"
    resp = httpx.get(url, timeout=timeout)
    return {"host": host, "url": url, "status_code": resp.status_code}


def preflight_openai(base_url: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["dns"] = assert_dns_reachability(base_url)
    out["http"] = assert_http_reachability(base_url)
    return out
