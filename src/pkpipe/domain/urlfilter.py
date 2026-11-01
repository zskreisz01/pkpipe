"""URL filter (constitution IV): http(s) URLs of public hosts that are not denied. Pure logic: names are
resolved only through the ``resolver`` the caller passes (the gateway passes DNS; flows pass none).

"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit


class UrlRejected(ValueError):
    """The URL is refused; the message is a short reason code (``private_network``, ``deny_list``, ...)."""


Resolver = Callable[[str], list[str]]
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home.arpa")


def _is_private(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return True  # not an address: refuse rather than guess
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


def parse_deny_list(text: str) -> list[str]:
    """One host or domain per line; ``#`` starts a comment. A domain also denies its sub domains."""
    entries = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if line:
            entries.append(line)
    return entries


def check_url(
    url: str,
    *,
    block_private_networks: bool = True,
    deny: Iterable[str] = (),
    resolver: Resolver | None = None,
) -> str:
    """The normalised URL, or :class:`UrlBlocked` with a short reason code in the message."""
    url = (url or "").strip()
    if not url or len(url) > 2048 or any(c.isspace() or ord(c) < 32 for c in url):
        raise UrlRejected("invalid_url")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UrlRejected("scheme_not_allowed")
    if parts.username or parts.password:
        raise UrlRejected("credentials_in_url")
    try:
        host = (parts.hostname or "").lower().rstrip(".")
        parts.port  # noqa: B018 - raises ValueError for an invalid port
    except ValueError:
        raise UrlRejected("invalid_url") from None
    if not host:
        raise UrlRejected("invalid_url")
    for entry in deny:
        if host == entry or host.endswith("." + entry):
            raise UrlRejected("deny_list")
    if block_private_networks:
        if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
            raise UrlRejected("private_network")
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            addresses = resolver(host) if resolver else []
        if any(_is_private(a) for a in addresses):
            raise UrlRejected("private_network")
    return url
