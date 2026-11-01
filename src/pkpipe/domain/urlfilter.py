"""URL filter (constitution IV): http(s) URLs of public hosts that are not denied. Pure logic: names are
resolved only through the ``resolver`` the caller passes (the gateway passes DNS; flows pass none).

"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit

from pkpipe.security.urlfilter import LOCAL_NAMES, LOCAL_SUFFIXES, is_public_address
from pkpipe.security.urlfilter import parse_deny_list as _parse_deny_list


class UrlRejected(ValueError):
    """The URL is refused; the message is a short reason code (``private_network``, ``deny_list``, ...)."""


Resolver = Callable[[str], list[str]]


def _is_private(addr: str) -> bool:
    """The address classification of secure fetch (spec 008): loopback, private, link-local, CGNAT, 6to4 and
    IPv4-mapped forms, multicast, reserved; anything that is not an address is refused rather than guessed."""
    return not is_public_address(addr)


def parse_deny_list(text: str) -> list[str]:
    """One host or domain per line; ``#`` comments, ``*.`` prefixes and URLs accepted (the same file format
    as secure fetch). A domain also denies its sub domains."""
    return sorted(_parse_deny_list(text))


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
        if host in LOCAL_NAMES or host.endswith(LOCAL_SUFFIXES):
            raise UrlRejected("private_network")
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            addresses = resolver(host) if resolver else []
        if any(_is_private(a) for a in addresses):
            raise UrlRejected("private_network")
    return url
