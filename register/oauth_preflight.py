# -*- coding: utf-8 -*-
"""Strict OAuth endpoint connectivity checks used before an expensive mint.

The check deliberately keeps certificate verification enabled.  A hostname
mismatch normally means the selected proxy node has mis-routed TLS/SNI; browser
fallback through the same node would only waste another minute.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from curl_cffi import requests

from cpa_schema import ISSUER

LogFn = Callable[[str], None]

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _cache_ttl() -> float:
    try:
        return max(0.0, min(float(os.environ.get("OAUTH_PREFLIGHT_CACHE_SEC", "45")), 300.0))
    except Exception:
        return 45.0


def _endpoint() -> str:
    """Allow an endpoint override without silently changing the default issuer."""
    env = str(os.environ.get("XAI_OIDC_ISSUER") or "").strip().rstrip("/")
    if env:
        return env
    config = Path(__file__).resolve().parent / "config.json"
    try:
        import json

        data = json.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
        configured = str(
            data.get("xai_oidc_issuer")
            or data.get("xaiOidcIssuer")
            or data.get("oauth_issuer")
            or ""
        ).strip().rstrip("/")
        if configured:
            return configured
    except Exception:
        pass
    return ISSUER.rstrip("/")


def _classify_error(error: BaseException | str) -> str:
    text = str(error or "").lower()
    if any(
        marker in text
        for marker in (
            "no alternative certificate subject name",
            "certificate subject name",
            "hostname mismatch",
            "doesn't match",
            "does not match",
            "curl: (60)",
        )
    ):
        return "tls_hostname_mismatch"
    if any(marker in text for marker in ("certificate verify", "self signed", "unknown ca")):
        return "tls_verify_failed"
    if any(marker in text for marker in ("timeout", "timed out")):
        return "network_timeout"
    if any(marker in text for marker in ("reset", "eof", "refused", "connect")):
        return "network_connect_failed"
    return "network_error"


def oauth_tls_preflight(
    proxy: str = "",
    *,
    log: LogFn | None = None,
    force: bool = False,
    timeout: float = 12.0,
) -> dict[str, Any]:
    """Validate TLS/SNI for the configured issuer through the selected proxy.

    Any HTTP response proves that TLS completed; 401/403/404 are therefore
    accepted.  Certificate and transport errors are returned as stable status
    values so the durable queue can retry them without launching Chromium.
    """
    issuer = _endpoint()
    proxy_s = str(proxy or "").strip()
    cache_key = f"{issuer}|{proxy_s}"
    now = time.time()
    ttl = _cache_ttl()
    if not force and ttl > 0:
        with _lock:
            cached = _cache.get(cache_key)
        if cached and now - cached[0] <= ttl:
            return dict(cached[1], cached=True)

    check_url = f"{issuer}/.well-known/openid-configuration"
    proxies = {"http": proxy_s, "https": proxy_s} if proxy_s else None
    try:
        response = requests.get(
            check_url,
            proxies=proxies,
            impersonate="chrome131",
            timeout=max(3.0, min(float(timeout), 30.0)),
            allow_redirects=False,
        )
        result: dict[str, Any] = {
            "ok": True,
            "status": "ok",
            "http_status": int(getattr(response, "status_code", 0) or 0),
            "issuer": issuer,
            "proxy": bool(proxy_s),
        }
        if log:
            log(
                f"[oauth-preflight] TLS OK issuer={issuer} "
                f"proxy={'on' if proxy_s else 'off'} http={result['http_status']}"
            )
    except Exception as exc:
        status = _classify_error(exc)
        result = {
            "ok": False,
            "status": status,
            "issuer": issuer,
            "proxy": bool(proxy_s),
            "error": str(exc)[:300],
        }
        if log:
            log(
                f"[oauth-preflight] TLS FAIL status={status} issuer={issuer} "
                f"proxy={'on' if proxy_s else 'off'} err={str(exc)[:180]}"
            )

    with _lock:
        _cache[cache_key] = (now, dict(result))
    return result


__all__ = ["oauth_tls_preflight"]
