from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from .config import SolverConfig
from .models import TaskSpec


class ValidationError(ValueError):
    pass


def _host_allowed(host: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return True
    return any(host == item or (item.startswith("*.") and host.endswith(item[1:])) for item in patterns)


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_task(spec: TaskSpec, config: SolverConfig) -> None:
    if not spec.url or not spec.sitekey:
        raise ValidationError("websiteURL and websiteKey are required")
    if len(spec.url) > 2_048 or len(spec.sitekey) > 256:
        raise ValidationError("task URL or sitekey is too long")
    if len(spec.action) > 128 or len(spec.cdata) > 2_048:
        raise ValidationError("action or cData is too long")

    parsed = urlsplit(spec.url)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValidationError("websiteURL has an invalid port") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValidationError("websiteURL must be an http(s) URL without credentials")
    host = parsed.hostname.rstrip(".").lower()
    if not _host_allowed(host, config.allowed_hosts):
        raise ValidationError("target host is not in TURNSTILE_ALLOWED_HOSTS")
    if config.allow_private_targets:
        return
    if host == "localhost" or host.endswith(".localhost"):
        raise ValidationError("private target URLs are disabled")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValidationError(f"target host cannot be resolved: {exc}") from exc
    if not addresses or any(not _is_public(address) for address in addresses):
        raise ValidationError("private target URLs are disabled")
