"""URL filter of the gateway: the pure rules of :mod:`pkpipe.domain.urlfilter` plus DNS resolution.

The backend fetches the page itself, so the check happens before any URL leaves the gateway. A name that
does not resolve is left to the backend (it cannot fetch it either).
"""

from __future__ import annotations

import socket
from collections.abc import Iterable

from pkpipe.domain.urlfilter import Resolver, UrlRejected, parse_deny_list
from pkpipe.domain.urlfilter import check_url as _check

from .errors import UrlBlocked


def _resolve(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except OSError:
        return []


def check_url(
    url: str,
    *,
    block_private_networks: bool = True,
    deny: Iterable[str] = (),
    resolver: Resolver = _resolve,
) -> str:
    """The normalised URL, or :class:`UrlBlocked` whose message is a short reason code."""
    try:
        return _check(url, block_private_networks=block_private_networks, deny=deny, resolver=resolver)
    except UrlRejected as exc:
        raise UrlBlocked(str(exc)) from None


__all__ = ["check_url", "parse_deny_list"]
