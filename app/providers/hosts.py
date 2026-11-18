"""Shared URL helpers: loopback check, http(s) validation, cleartext check."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


def is_loopback_url(url: str | None) -> bool:
    """True for `localhost`, `127.0.0.0/8` and `::1`; false for every other host."""
    if not url:
        return False
    host = (urlsplit(url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_http_url(url: str | None) -> bool:
    """True for an `http://` or `https://` URL with a host."""
    if not url:
        return False
    parts = urlsplit(url.strip())
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def is_cleartext_remote(url: str | None) -> bool:
    """Plain `http://` to a host other than this machine: keys and prompts travel unencrypted."""
    return bool(url) and urlsplit(url).scheme == "http" and not is_loopback_url(url)


def host_of(url: str | None) -> str | None:
    return urlsplit(url).hostname if url else None
