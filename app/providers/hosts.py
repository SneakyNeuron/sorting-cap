"""Shared helper: does a URL point to this machine (loopback)?"""

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
