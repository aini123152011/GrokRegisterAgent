# -*- coding: utf-8 -*-
"""Turnstile solver client with local and YesCaptcha providers.

The public ``solve_turnstile`` function remains backward compatible and returns
only a token. New code can use ``solve_turnstile_result`` for typed status and
error details.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen


LogFn = Callable[[str], None]
Provider = Literal["local", "yescaptcha", "none"]
_DEFAULT_SITE_URL = "https://accounts.x.ai"
_DEFAULT_SITEKEY = "0x4AAAAAAAhr9JGVDZbrZOo0"
_YESCAPTCHA_API = "https://api.yescaptcha.com"
_TERMINAL_STATUSES = {"failed", "error", "expired", "cancelled"}
_DEFAULT_TASK_TIMEOUT = 90.0
_DEFAULT_CLIENT_GRACE = 15.0


@dataclass(frozen=True, slots=True)
class SolveResult:
    status: Literal["ready", "failed"]
    provider: Provider
    token: str = ""
    task_id: str = ""
    error_code: str = ""
    error: str = ""
    elapsed_ms: int = 0

    @property
    def solved(self) -> bool:
        return self.status == "ready" and bool(self.token)


class SolverApiError(RuntimeError):
    def __init__(self, status: int, data: dict[str, Any], message: str = "") -> None:
        self.status = status
        self.data = data
        detail = str(data.get("errorDescription") or data.get("message") or message or f"HTTP {status}")
        super().__init__(detail)


def _log(log: Optional[LogFn], message: str) -> None:
    if log:
        try:
            log(message)
            return
        except Exception:
            pass
    print(message, flush=True)


def _load_cfg() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def solver_enabled(cfg: Optional[dict[str, Any]] = None) -> bool:
    config = cfg if cfg is not None else _load_cfg()
    raw = os.getenv("TURNSTILE_SOLVER_ENABLED", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(config.get("turnstile_solver_enabled"))


def solver_url(cfg: Optional[dict[str, Any]] = None) -> str:
    config = cfg if cfg is not None else _load_cfg()
    return (
        os.getenv("TURNSTILE_SOLVER_URL", "").strip()
        or str(config.get("turnstile_solver_url") or "").strip()
        or "http://turnstile-solver:5072"
    ).rstrip("/")


def solver_key(cfg: Optional[dict[str, Any]] = None) -> str:
    config = cfg if cfg is not None else _load_cfg()
    return os.getenv("TURNSTILE_API_KEY", "").strip() or str(config.get("turnstile_solver_key") or "").strip()


def yescaptcha_key(cfg: Optional[dict[str, Any]] = None) -> str:
    config = cfg if cfg is not None else _load_cfg()
    return os.getenv("YESCAPTCHA_KEY", "").strip() or str(config.get("yescaptcha_key") or "").strip()


def _positive_timeout(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def solver_task_timeout(cfg: Optional[dict[str, Any]] = None) -> float:
    """Return the local solver's server-side task deadline."""
    config = cfg if cfg is not None else _load_cfg()
    raw = os.getenv("TURNSTILE_TASK_TIMEOUT", "").strip()
    if not raw:
        raw = config.get("turnstile_task_timeout", _DEFAULT_TASK_TIMEOUT)
    return min(300.0, max(10.0, _positive_timeout(raw, _DEFAULT_TASK_TIMEOUT)))


def solver_client_wait_timeout(cfg: Optional[dict[str, Any]] = None) -> float:
    """Return a client deadline that cannot expire before the local solver.

    The browser/page timeout is deliberately not used here.  A local task may
    legitimately consume its full server-side deadline, so the caller keeps a
    small grace window for the final poll and network latency.
    """
    config = cfg if cfg is not None else _load_cfg()
    minimum = solver_task_timeout(config) + _DEFAULT_CLIENT_GRACE
    raw = os.getenv("TURNSTILE_CLIENT_WAIT_TIMEOUT", "").strip()
    if not raw:
        raw = config.get("turnstile_client_wait_timeout", minimum)
    return min(330.0, max(minimum, _positive_timeout(raw, minimum)))


def sitekey_default(cfg: Optional[dict[str, Any]] = None) -> str:
    config = cfg if cfg is not None else _load_cfg()
    return str(config.get("turnstile_sitekey") or _DEFAULT_SITEKEY).strip() or _DEFAULT_SITEKEY


def _http_json(
    method: str,
    url: str,
    *,
    body: Optional[dict[str, Any]] = None,
    timeout: float = 20.0,
    api_key: str = "",
) -> dict[str, Any]:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Accept": "application/json",
        "User-Agent": "GrokRegisterAgent/turnstile-client-v2",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["X-API-Key"] = api_key
    request = Request(url, data=payload, headers=headers, method=method.upper())
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = int(getattr(response, "status", 200) or 200)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            data = {}
        raise SolverApiError(exc.code, data if isinstance(data, dict) else {}, raw[:300]) from exc
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise SolverApiError(status, {}, "solver returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise SolverApiError(status, {}, "solver returned a non-object response")
    return data


def probe_solver(url: str = "", *, timeout: float = 5.0, log: Optional[LogFn] = None) -> dict[str, Any]:
    base = (url or solver_url()).rstrip("/")
    started = time.monotonic()
    try:
        data = _http_json("GET", f"{base}/health", timeout=timeout)
        latency = int((time.monotonic() - started) * 1_000)
        ok = bool(data.get("ok"))
        return {
            "ok": ok,
            "message": f"solver {data.get('status', 'reachable')}" if ok else "solver health check failed",
            "url": base,
            "latency_ms": latency,
            "backend": data.get("backend", ""),
            "queue_depth": data.get("queueDepth", 0),
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": str(exc) or "connection failed",
            "url": base,
            "latency_ms": int((time.monotonic() - started) * 1_000),
        }


def _task_payload(
    siteurl: str,
    sitekey: str,
    *,
    proxy: str,
    action: str,
    cdata: str,
) -> dict[str, Any]:
    task: dict[str, Any] = {
        "type": "TurnstileTask" if proxy else "TurnstileTaskProxyless",
        "websiteURL": siteurl,
        "websiteKey": sitekey,
    }
    if proxy:
        task["proxy"] = proxy
    if action:
        task["action"] = action
    if cdata:
        task["cData"] = cdata
    return task


def _poll_provider(
    base_url: str,
    task_id: str,
    *,
    client_key: str,
    provider: Literal["local", "yescaptcha"],
    max_wait: float,
    log: Optional[LogFn],
) -> SolveResult:
    started = time.monotonic()
    deadline = started + max(10.0, float(max_wait))
    delay = 1.0 if provider == "local" else 2.0
    while time.monotonic() < deadline:
        try:
            data = _http_json(
                "POST",
                f"{base_url}/getTaskResult",
                body={"clientKey": client_key, "taskId": task_id},
                timeout=min(20.0, max(3.0, deadline - time.monotonic())),
                api_key=client_key if provider == "local" else "",
            )
        except SolverApiError as exc:
            if exc.status >= 500:
                _log(log, f"[{provider}] transient polling HTTP {exc.status}: {exc}")
                time.sleep(delay)
                continue
            return SolveResult(
                "failed",
                provider,
                task_id=task_id,
                error_code=str(exc.data.get("errorCode") or f"HTTP_{exc.status}"),
                error=str(exc),
                elapsed_ms=int((time.monotonic() - started) * 1_000),
            )
        except (TimeoutError, OSError) as exc:
            _log(log, f"[{provider}] transient polling error: {exc}")
            time.sleep(delay)
            continue

        status = str(data.get("status") or "").strip().lower()
        error_id = int(data.get("errorId") or 0)
        if error_id or status in _TERMINAL_STATUSES:
            return SolveResult(
                "failed",
                provider,
                task_id=task_id,
                error_code=str(data.get("errorCode") or "ERROR_TASK_FAILED"),
                error=str(data.get("errorDescription") or status or "solver task failed"),
                elapsed_ms=int((time.monotonic() - started) * 1_000),
            )
        if status == "ready" or data.get("solution"):
            solution = data.get("solution") if isinstance(data.get("solution"), dict) else {}
            token = str(solution.get("token") or solution.get("gRecaptchaResponse") or data.get("token") or "").strip()
            if token:
                return SolveResult(
                    "ready",
                    provider,
                    token=token,
                    task_id=task_id,
                    elapsed_ms=int((time.monotonic() - started) * 1_000),
                )
            return SolveResult("failed", provider, task_id=task_id, error_code="ERROR_EMPTY_TOKEN", error="ready response had no token")
        time.sleep(delay)
    return SolveResult(
        "failed",
        provider,
        task_id=task_id,
        error_code="ERROR_CLIENT_TIMEOUT",
        error=f"no terminal result within {max_wait:.0f}s",
        elapsed_ms=int((time.monotonic() - started) * 1_000),
    )


def _solve_provider(
    provider: Literal["local", "yescaptcha"],
    siteurl: str,
    sitekey: str,
    *,
    base_url: str,
    client_key: str,
    proxy: str,
    action: str,
    cdata: str,
    max_wait: float,
    log: Optional[LogFn],
) -> SolveResult:
    started = time.monotonic()
    try:
        data = _http_json(
            "POST",
            f"{base_url}/createTask",
            body={"clientKey": client_key, "task": _task_payload(siteurl, sitekey, proxy=proxy, action=action, cdata=cdata)},
            timeout=30.0,
            api_key=client_key if provider == "local" else "",
        )
    except Exception as exc:
        return SolveResult(
            "failed",
            provider,
            error_code="ERROR_CREATE_TASK",
            error=str(exc),
            elapsed_ms=int((time.monotonic() - started) * 1_000),
        )
    if int(data.get("errorId") or 0):
        return SolveResult(
            "failed",
            provider,
            error_code=str(data.get("errorCode") or "ERROR_CREATE_TASK"),
            error=str(data.get("errorDescription") or "createTask failed"),
        )
    task_id = str(data.get("taskId") or "").strip()
    if not task_id:
        return SolveResult("failed", provider, error_code="ERROR_NO_TASK_ID", error="createTask returned no taskId")
    _log(log, f"[{provider}] taskId={task_id[:12]}…")
    return _poll_provider(
        base_url,
        task_id,
        client_key=client_key,
        provider=provider,
        max_wait=max_wait,
        log=log,
    )


def solve_turnstile_result(
    siteurl: str = _DEFAULT_SITE_URL,
    sitekey: str = "",
    *,
    prefer: str = "auto",
    max_wait: float = 90.0,
    proxy: str = "",
    action: str = "",
    cdata: str = "",
    log: Optional[LogFn] = None,
) -> SolveResult:
    config = _load_cfg()
    url = (siteurl or _DEFAULT_SITE_URL).strip()
    key = (sitekey or sitekey_default(config)).strip()
    preferred = (prefer or "auto").strip().lower()
    providers: list[Literal["local", "yescaptcha"]] = []
    if preferred == "local":
        providers = ["local"]
    elif preferred == "yescaptcha":
        providers = ["yescaptcha"]
    else:
        if solver_enabled(config):
            providers.append("local")
        if yescaptcha_key(config):
            providers.append("yescaptcha")
    if not providers:
        return SolveResult("failed", "none", error_code="ERROR_NOT_CONFIGURED", error="no solver provider is enabled")

    last = SolveResult("failed", "none", error_code="ERROR_NOT_CONFIGURED", error="no provider attempted")
    for provider in providers:
        client_key = solver_key(config) if provider == "local" else yescaptcha_key(config)
        if provider == "yescaptcha" and not client_key:
            last = SolveResult("failed", provider, error_code="ERROR_NO_CLIENT_KEY", error="YesCaptcha key is empty")
            continue
        try:
            last = _solve_provider(
                provider,
                url,
                key,
                base_url=solver_url(config) if provider == "local" else _YESCAPTCHA_API,
                client_key=client_key,
                proxy=proxy,
                action=action,
                cdata=cdata,
                max_wait=max_wait,
                log=log,
            )
        except Exception as exc:
            last = SolveResult(
                "failed",
                provider,
                error_code="ERROR_CLIENT",
                error=str(exc),
            )
        if last.solved:
            _log(log, f"[{provider}] token received len={len(last.token)}")
            return last
        _log(log, f"[{provider}] failed {last.error_code}: {last.error}")
    return last


def solve_turnstile(
    siteurl: str = _DEFAULT_SITE_URL,
    sitekey: str = "",
    *,
    prefer: str = "auto",
    max_wait: float = 90.0,
    proxy: str = "",
    action: str = "",
    cdata: str = "",
    log: Optional[LogFn] = None,
) -> str:
    """Backward-compatible token-only wrapper."""
    result = solve_turnstile_result(
        siteurl,
        sitekey,
        prefer=prefer,
        max_wait=max_wait,
        proxy=proxy,
        action=action,
        cdata=cdata,
        log=log,
    )
    return result.token if result.solved else ""
