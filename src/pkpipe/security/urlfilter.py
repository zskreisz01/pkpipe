"""URL filter of secure fetch (spec 008 FR-001): scheme, deny-list, private network block, size limit.

Pure, deterministic code (constitution V). The same :class:`UrlPolicy` runs twice: in the ``url_check`` step
before anything is fetched, and in the web-fetch server's filtering proxy on *every* request the browser
makes (redirect hops, sub-resources, scripts), after DNS resolution, so a redirect to a private address or a
DNS name that resolves into the private network is blocked as well.

Test-only allowance: :attr:`UrlPolicy.test_loopback` lists exact ``(host, port)`` pairs on a loopback address
that are allowed although loopback is private. It exists only so the test suite can fetch synthetic pages
from a local fixture server. It is never read from config (``security.url_filter`` has no such key, unknown
keys are rejected); only code builds it (the test composition root) or, for the web-fetch server process,
the ``PKPIPE_WEBFETCH_TEST_LOOPBACK`` environment variable, which :func:`parse_test_loopback` accepts only
for loopback IP literals with an explicit port.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

#: Address families of ``socket.getaddrinfo``; a resolver returns the IP strings of a host.
Resolver = Callable[[str], list[str]]

ALLOWED_SCHEMES = frozenset({"http", "https"})
MAX_URL_LENGTH = 2048
#: Host names that always mean this machine or its private network, whatever DNS says.
LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})
LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet")


class Reason:
    """Reason codes of a blocked URL (recorded on spans and in the verdict, never the URL text)."""

    SCHEME = "scheme_not_allowed"
    MALFORMED = "malformed_url"
    CREDENTIALS = "credentials_in_url"
    DENY_LIST = "deny_list"
    PRIVATE = "private_network"
    UNRESOLVABLE = "unresolvable"
    SIZE = "size_limit"
    TOO_LONG = "url_too_long"


#: Reasons that are a security flag; the others (unresolvable host) are a failed fetch.
FLAG_REASONS = frozenset(
    {Reason.SCHEME, Reason.CREDENTIALS, Reason.DENY_LIST, Reason.PRIVATE, Reason.SIZE, Reason.TOO_LONG}
)


@dataclass(frozen=True)
class UrlDecision:
    allowed: bool
    reason: str | None = None
    host: str | None = None
    port: int | None = None
    addresses: tuple[str, ...] = ()

    @property
    def flag(self) -> bool:
        """Whether the block is a security flag (deny-list, private network, ...) rather than a failure."""
        return not self.allowed and self.reason in FLAG_REASONS


def system_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({str(info[4][0]).split("%", 1)[0] for info in infos})


def parse_deny_list(text: str | None) -> frozenset[str]:
    """Domains of a deny-list file: one per line, ``#`` comments, ``*.`` prefixes and URLs accepted."""
    out: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip().lower()
        if not line:
            continue
        if "://" in line:
            line = urlsplit(line).hostname or ""
        line = line.removeprefix("*.").strip(".")
        if line:
            out.add(line)
    return frozenset(out)


def parse_test_loopback(value: str | None) -> frozenset[tuple[str, int]]:
    """``127.0.0.1:8123,[::1]:9000`` -> pairs. Anything but a loopback IP literal with a port fails."""
    pairs: set[tuple[str, int]] = set()
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        host, sep, port = part.rpartition(":")
        host = host.strip("[]")
        try:
            ip = ipaddress.ip_address(host)
            port_no = int(port)
        except ValueError as exc:
            raise ValueError(f"test loopback allowance '{part}' must be <loopback ip>:<port>") from exc
        if not sep or not ip.is_loopback or not 0 < port_no < 65536:
            raise ValueError(f"test loopback allowance '{part}' must be a loopback address with a port")
        pairs.add((str(ip), port_no))
    return frozenset(pairs)


def is_public_address(address: str) -> bool:
    """False for loopback, private, link-local (cloud metadata), CGNAT, multicast, reserved, unspecified."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address) and ip.sixtofour is not None:
        ip = ip.sixtofour
    if ip in ipaddress.ip_network("100.64.0.0/10"):  # carrier-grade NAT
        return False
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def _deny_hit(host: str, deny: Iterable[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in deny)


def _ip_literal(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    # legacy forms browsers still accept: 2130706433, 0x7f000001, 0177.0.0.1, 127.1
    try:
        return str(ipaddress.IPv4Address(socket.inet_aton(host)))
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class UrlPolicy:
    """What may be fetched. Built from ``security.url_filter`` plus the deny-list file of the config."""

    block_private_networks: bool = True
    max_bytes: int = 2_000_000
    deny: frozenset[str] = frozenset()
    test_loopback: frozenset[tuple[str, int]] = field(default_factory=frozenset)

    @classmethod
    def from_config(
        cls,
        url_filter: dict | None,
        deny_list_text: str | None = None,
        *,
        test_loopback: Iterable[tuple[str, int]] = (),
    ) -> UrlPolicy:
        cfg = url_filter or {}
        return cls(
            block_private_networks=bool(cfg.get("block_private_networks", True)),
            max_bytes=int(cfg.get("max_bytes", 2_000_000)),
            deny=parse_deny_list(deny_list_text),
            test_loopback=frozenset(test_loopback),
        )

    def _allowed_loopback(self, address: str, port: int) -> bool:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return ip.is_loopback and (str(ip), port) in self.test_loopback

    def check_address(self, address: str, port: int) -> str | None:
        """Reason code when a connection to ``address:port`` is not allowed, else None."""
        if not self.block_private_networks or is_public_address(address):
            return None
        return None if self._allowed_loopback(address, port) else Reason.PRIVATE

    def check_host(self, host: str, port: int, resolve: Resolver | None = None) -> UrlDecision:
        """Deny-list and private-network check of one host (no scheme): used for proxy CONNECT targets."""
        host = host.strip().strip("[]").lower().rstrip(".")
        if not host:
            return UrlDecision(False, Reason.MALFORMED)
        if _deny_hit(host, self.deny):
            return UrlDecision(False, Reason.DENY_LIST, host, port)
        literal = _ip_literal(host)
        if literal is None and self.block_private_networks:
            if host in LOCAL_NAMES or host.endswith(LOCAL_SUFFIXES) or "." not in host:
                return UrlDecision(False, Reason.PRIVATE, host, port)
        if literal is not None:
            addresses = [literal]
        else:
            try:
                addresses = (resolve or system_resolver)(host)
            except (OSError, UnicodeError, ValueError):
                return UrlDecision(False, Reason.UNRESOLVABLE, host, port)
            if not addresses:
                return UrlDecision(False, Reason.UNRESOLVABLE, host, port)
        for address in addresses:
            reason = self.check_address(address, port)
            if reason:
                return UrlDecision(False, reason, host, port, tuple(addresses))
        return UrlDecision(True, None, host, port, tuple(addresses))

    def check_url(self, url: str, resolve: Resolver | None = None) -> UrlDecision:
        """Full check of a URL: length, scheme, credentials, deny-list, every resolved address."""
        if not isinstance(url, str) or not url.strip():
            return UrlDecision(False, Reason.MALFORMED)
        url = url.strip()
        if len(url) > MAX_URL_LENGTH:
            return UrlDecision(False, Reason.TOO_LONG)
        if any(ord(c) < 0x21 for c in url):
            return UrlDecision(False, Reason.MALFORMED)
        try:
            parts = urlsplit(url)
            port = parts.port
            host = parts.hostname
        except ValueError:
            return UrlDecision(False, Reason.MALFORMED)
        if parts.scheme.lower() not in ALLOWED_SCHEMES:
            return UrlDecision(False, Reason.SCHEME)
        if parts.username or parts.password:
            return UrlDecision(False, Reason.CREDENTIALS)
        if not host:
            return UrlDecision(False, Reason.MALFORMED)
        port = port or (443 if parts.scheme.lower() == "https" else 80)
        return self.check_host(host, port, resolve)

    def check_size(self, size: int | None) -> str | None:
        return Reason.SIZE if size is not None and size > self.max_bytes else None
