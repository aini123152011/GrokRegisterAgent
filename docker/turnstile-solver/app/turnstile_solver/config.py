from __future__ import annotations

import os
from dataclasses import dataclass, replace


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True, slots=True)
class SolverConfig:
    host: str = "127.0.0.1"
    port: int = 5072
    backend: str = "chromium"
    headless: bool = True
    workers: int = 2
    queue_size: int = 16
    task_timeout_seconds: int = 90
    navigation_timeout_ms: int = 45_000
    automatic_wait_ms: int = 8_000
    interaction_wait_ms: int = 15_000
    max_click_attempts: int = 3
    result_ttl_seconds: int = 600
    token_ttl_seconds: int = 120
    max_results: int = 1_000
    api_key: str = ""
    admin_key: str = ""
    allowed_hosts: tuple[str, ...] = ()
    allow_private_targets: bool = False
    proxy_support: bool = True
    debug: bool = False

    @classmethod
    def from_env(cls) -> "SolverConfig":
        hosts = tuple(
            item.strip().lower()
            for item in os.getenv("TURNSTILE_ALLOWED_HOSTS", "").split(",")
            if item.strip()
        )
        return cls(
            host=os.getenv("HOST", os.getenv("TURNSTILE_HOST", "127.0.0.1")),
            port=_int("PORT", 5072, 1, 65535),
            backend=os.getenv("BROWSER_TYPE", "chromium").strip().lower(),
            headless=not _bool("TURNSTILE_HEADED", False),
            workers=_int("THREAD", 2, 1, 16),
            queue_size=_int("TURNSTILE_QUEUE_SIZE", 16, 1, 1_000),
            task_timeout_seconds=_int("TURNSTILE_TASK_TIMEOUT", 90, 10, 300),
            navigation_timeout_ms=_int("TURNSTILE_NAVIGATION_TIMEOUT_MS", 45_000, 5_000, 180_000),
            automatic_wait_ms=_int("TURNSTILE_AUTOMATIC_WAIT_MS", 8_000, 500, 60_000),
            interaction_wait_ms=_int("TURNSTILE_INTERACTION_WAIT_MS", 15_000, 1_000, 60_000),
            max_click_attempts=_int("TURNSTILE_MAX_CLICKS", 3, 1, 8),
            result_ttl_seconds=_int("TURNSTILE_RESULT_TTL", 600, 30, 86_400),
            token_ttl_seconds=_int("TURNSTILE_TOKEN_TTL", 120, 15, 3_600),
            max_results=_int("TURNSTILE_MAX_RESULTS", 1_000, 10, 100_000),
            api_key=os.getenv("TURNSTILE_API_KEY", "").strip(),
            admin_key=os.getenv("TURNSTILE_ADMIN_KEY", "").strip(),
            allowed_hosts=hosts,
            allow_private_targets=_bool("TURNSTILE_ALLOW_PRIVATE_TARGETS", False),
            proxy_support=_bool("PROXY", True),
            debug=_bool("DEBUG", False),
        )

    def with_cli(self, **changes: object) -> "SolverConfig":
        return replace(self, **{key: value for key, value in changes.items() if value is not None})
