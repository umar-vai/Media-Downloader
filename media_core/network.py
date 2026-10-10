from __future__ import annotations

import os
import re
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class ProxyRoute:
    url: str
    source: str


def _normalize_proxy_url(value: str, *, scheme_hint: str = "http") -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        scheme = "socks5" if scheme_hint.lower().startswith("socks") else "http"
        raw = f"{scheme}://{raw}"
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if not parsed.scheme or not parsed.hostname:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _parse_windows_proxy_server(value: str) -> str:
    """Parse WinINet ProxyServer values like host:port or http=...;https=..."""
    raw = str(value or "").strip()
    if not raw:
        return ""

    if "=" not in raw:
        return _normalize_proxy_url(raw)

    entries: dict[str, str] = {}
    for piece in raw.split(";"):
        if "=" not in piece:
            continue
        key, proxy = piece.split("=", 1)
        key = key.strip().lower()
        proxy = proxy.strip()
        if key and proxy:
            entries[key] = proxy

    for key in ("https", "http", "socks", "socks5"):
        if key in entries:
            return _normalize_proxy_url(entries[key], scheme_hint=key)
    return ""


def _environment_proxy() -> ProxyRoute | None:
    for key in (
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        value = os.environ.get(key)
        normalized = _normalize_proxy_url(value or "", scheme_hint="socks" if "ALL" in key.upper() else "http")
        if normalized:
            return ProxyRoute(normalized, "environment")
    return None


def _windows_registry_proxy() -> ProxyRoute | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not bool(enabled):
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        normalized = _parse_windows_proxy_server(str(server or ""))
        if normalized:
            return ProxyRoute(normalized, "windows")
    except (OSError, ValueError):
        return None
    return None


def active_proxy_route() -> ProxyRoute | None:
    """Return the proxy route Media Downloader should pass to non-WinINet clients.

    Environment variables take precedence. On Windows, the user's System Proxy
    (WinINet/Internet Settings) is bridged into yt-dlp/curl-style clients that
    do not reliably read it themselves.
    """
    env = _environment_proxy()
    if env is not None:
        return env

    windows = _windows_registry_proxy()
    if windows is not None:
        return windows

    # Python's platform proxy discovery provides a final cross-platform fallback.
    try:
        proxies = urllib.request.getproxies()
    except Exception:
        proxies = {}
    for key in ("https", "http", "all"):
        value = proxies.get(key)
        normalized = _normalize_proxy_url(str(value or ""), scheme_hint="socks" if key == "all" else "http")
        if normalized:
            return ProxyRoute(normalized, "system")
    return None


def active_proxy_url() -> str:
    route = active_proxy_route()
    return route.url if route is not None else ""


def yt_dlp_proxy_options() -> dict[str, str]:
    proxy = active_proxy_url()
    return {"proxy": proxy} if proxy else {}


def safe_proxy_label() -> str:
    route = active_proxy_route()
    if route is None:
        return "Direct network"
    try:
        parsed = urlsplit(route.url)
        host = parsed.hostname or "proxy"
        port = f":{parsed.port}" if parsed.port else ""
        source = "System proxy" if route.source in {"windows", "system"} else "Proxy"
        return f"{source} active • {host}{port}"
    except Exception:
        return "Proxy active"


def proxy_for_subprocess() -> str:
    return active_proxy_url()


__all__ = [
    "ProxyRoute",
    "active_proxy_route",
    "active_proxy_url",
    "yt_dlp_proxy_options",
    "safe_proxy_label",
    "proxy_for_subprocess",
]
