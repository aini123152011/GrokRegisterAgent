from __future__ import annotations

from urllib.parse import unquote, urlsplit


class ProxyError(ValueError):
    pass


def parse_proxy(raw: str) -> dict[str, str] | None:
    value = (raw or "").strip()
    if not value:
        return None
    aliases = {"soket5": "socks5", "socket5": "socks5", "socks5h": "socks5"}
    if "://" not in value:
        parts = value.split(":")
        if len(parts) == 2:
            value = f"http://{value}"
        elif len(parts) >= 4:
            host, port, user = parts[:3]
            password = ":".join(parts[3:])
            value = f"http://{user}:{password}@{host}:{port}"
        else:
            raise ProxyError("proxy must be URL, host:port, or host:port:user:password")
    parsed = urlsplit(value)
    scheme = aliases.get(parsed.scheme.lower(), parsed.scheme.lower())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProxyError("proxy has an invalid port") from exc
    if scheme not in {"http", "https", "socks4", "socks5"} or not parsed.hostname or not port:
        raise ProxyError("unsupported or incomplete proxy URL")
    result = {"server": f"{scheme}://{parsed.hostname}:{port}"}
    if parsed.username:
        result["username"] = unquote(parsed.username)
    if parsed.password is not None:
        result["password"] = unquote(parsed.password)
    return result


def redact_proxy(raw: str) -> str:
    try:
        parsed = urlsplit(raw)
        if parsed.hostname:
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme or 'http'}://***@{parsed.hostname}{port}"
    except Exception:
        pass
    return "configured" if raw else ""
