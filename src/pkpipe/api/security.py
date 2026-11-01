"""Access control of the config UI and the app MCP server (spec 014 FR-001).

* Bind only to loopback or a Tailscale address (:func:`check_bind`); a token is always required.
* Two bearer tokens: the **owner** token (UI and every MCP tool) and an optional **agent** token that may only
  run flows and read triage and runs over MCP. Owner decisions (confirm, override, quarantine release,
  config publish) need the owner token. Tokens live in a 0600 file in the data directory or in the
  environment, never in config.
* Browser access uses a login (owner token) that sets an HttpOnly, SameSite=Strict session cookie. Cookie
  requests that change state also need the CSRF header and a same-origin ``Origin``.
* Every request's ``Host`` must be on the allowlist (DNS rebinding), responses carry strict security headers.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

TOKEN_ENV = "PKPIPE_TOKEN"
AGENT_TOKEN_ENV = "PKPIPE_AGENT_TOKEN"
MIN_TOKEN_LENGTH = 24
SESSION_COOKIE = "pkpipe_session"
CSRF_HEADER = "x-csrf-token"
SESSION_TTL_S = 12 * 3600
TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
OWNER, AGENT = "owner", "agent"


class BindRefused(ValueError):
    """The requested listen address is neither loopback nor Tailscale."""


def is_loopback(host: str) -> bool:
    try:
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_tailscale(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip in TAILSCALE_V4 or ip in TAILSCALE_V6


def check_bind(host: str) -> str:
    """Return ``host`` when the UI may listen on it (loopback or Tailscale), else raise BindRefused."""
    if is_loopback(host) or is_tailscale(host):
        return host
    raise BindRefused(
        f"refusing to bind {host!r}: the pkpipe UI listens on 127.0.0.1 (default) or a Tailscale address only"
    )


@dataclass(frozen=True)
class Tokens:
    owner: str
    agent: str | None = None

    def scope_of(self, presented: str | None) -> str | None:
        """``owner`` / ``agent`` for a valid token (constant time), else None."""
        if not presented:
            return None
        given = presented.encode()
        if hmac.compare_digest(given, self.owner.encode()):
            return OWNER
        if self.agent and hmac.compare_digest(given, self.agent.encode()):
            return AGENT
        return None

    @classmethod
    def load(cls, data_dir: Path, environ: Mapping[str, str] | None = None) -> Tokens:
        """Env tokens win; otherwise ``<data_dir>/tokens.json`` (created 0600 on first use)."""
        env = os.environ if environ is None else environ
        owner, agent = env.get(TOKEN_ENV), env.get(AGENT_TOKEN_ENV)
        for name, value in ((TOKEN_ENV, owner), (AGENT_TOKEN_ENV, agent)):
            if value is not None and len(value) < MIN_TOKEN_LENGTH:
                raise ValueError(f"{name} must be at least {MIN_TOKEN_LENGTH} characters")
        if owner:
            if agent and agent == owner:
                raise ValueError(f"{AGENT_TOKEN_ENV} must differ from {TOKEN_ENV}")
            return cls(owner, agent or None)
        path = data_dir / "tokens.json"
        if path.is_file():
            stored = json.loads(path.read_text(encoding="utf-8"))
            return cls(stored["owner"], stored.get("agent"))
        data_dir.mkdir(parents=True, exist_ok=True)
        fresh = {"owner": secrets.token_urlsafe(32), "agent": secrets.token_urlsafe(32)}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(fresh, fh)
        return cls(fresh["owner"], fresh["agent"])


class Sessions:
    """In-memory browser sessions (a restart logs out). The CSRF token is an HMAC of the session id."""

    def __init__(self, ttl_s: int = SESSION_TTL_S):
        self.ttl_s = ttl_s
        self._secret = secrets.token_bytes(32)
        self._sessions: dict[str, float] = {}

    def create(self) -> str:
        sid = secrets.token_urlsafe(32)
        self._sessions[sid] = time.monotonic() + self.ttl_s
        return sid

    def valid(self, sid: str | None) -> bool:
        if not sid:
            return False
        expires = self._sessions.get(sid)
        if expires is None:
            return False
        if expires < time.monotonic():
            self._sessions.pop(sid, None)
            return False
        return True

    def drop(self, sid: str | None) -> None:
        self._sessions.pop(sid or "", None)

    def csrf(self, sid: str) -> str:
        return hmac.new(self._secret, sid.encode(), hashlib.sha256).hexdigest()

    def csrf_ok(self, sid: str, presented: str | None) -> bool:
        return bool(presented) and hmac.compare_digest(self.csrf(sid), presented or "")


class LoginThrottle:
    """At most ``limit`` failed logins per ``window_s``; keeps guessing the token impractical."""

    def __init__(self, limit: int = 5, window_s: float = 60.0):
        self.limit, self.window_s = limit, window_s
        self._fails: list[float] = []

    def blocked(self) -> bool:
        now = time.monotonic()
        self._fails = [t for t in self._fails if now - t < self.window_s]
        return len(self._fails) >= self.limit

    def failed(self) -> None:
        self._fails.append(time.monotonic())


def allowed_hosts(bind_host: str, port: int | None = None, extra: Iterable[str] = ()) -> frozenset[str]:
    """Host header values (without port) the app answers to."""
    hosts = {"127.0.0.1", "localhost", "[::1]", "::1"}
    if bind_host and bind_host not in ("0.0.0.0", "::"):
        hosts.add(bind_host if ":" not in bind_host else f"[{bind_host}]")
    hosts.update(h.strip().lower() for h in extra if h.strip())
    return frozenset(hosts)


def host_of(header: str | None) -> str:
    """Host name of a ``Host`` header value, port stripped, lower case."""
    value = (header or "").strip().lower()
    if value.startswith("["):
        return value[: value.find("]") + 1]
    return value.split(":", 1)[0]


def origin_matches_host(origin: str | None, host_header: str | None) -> bool:
    if origin is None:
        return True
    if origin == "null":
        return False
    return urlsplit(origin).netloc.lower() == (host_header or "").strip().lower()


def cookie_value(cookie_header: str | None, name: str) -> str | None:
    if not cookie_header:
        return None
    jar: SimpleCookie = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:  # noqa: BLE001 - malformed cookie header is simply "no cookie"
        return None
    morsel = jar.get(name)
    return morsel.value if morsel else None


SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
    "cache-control": "no-store",
}

#: paths served without authentication (the login form, static assets and a bare liveness probe)
PUBLIC_PATHS = ("/login", "/api/health")
PUBLIC_PREFIXES = ("/static/",)
#: what the agent token may reach
AGENT_PATHS = ("/mcp", "/api/health")


class GuardMiddleware:
    """Pure ASGI middleware: Host allowlist, authentication, scope, CSRF/Origin and security headers."""

    def __init__(self, app, *, tokens: Tokens, sessions: Sessions, hosts: frozenset[str]):
        self.app, self.tokens, self.sessions, self.hosts = app, tokens, sessions, hosts

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":  # the app has none: never let one past the guard
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        path, method = scope["path"], scope["method"]

        async def reply(status: int, body: str, extra: dict[str, str] | None = None, ctype="text/plain"):
            hdrs = {**SECURITY_HEADERS, "content-type": f"{ctype}; charset=utf-8", **(extra or {})}
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [(k.encode(), v.encode()) for k, v in hdrs.items()],
                }
            )
            await send({"type": "http.response.body", "body": body.encode()})

        if host_of(headers.get("host")) not in self.hosts:
            await reply(421, "unknown host")
            return

        principal, via, sid = None, None, None
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            principal = self.tokens.scope_of(auth[7:].strip())
            via = "bearer"
            if principal is None:
                await reply(
                    401, '{"error":"invalid token"}', {"www-authenticate": "Bearer"}, "application/json"
                )
                return
        elif not path.startswith("/mcp"):  # an ambient browser cookie never authorizes the MCP endpoint
            sid = cookie_value(headers.get("cookie"), SESSION_COOKIE)
            if self.sessions.valid(sid):
                principal, via = OWNER, "session"

        public = path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)
        if principal is None and not public:
            if path.startswith(("/api/", "/mcp")):
                await reply(
                    401, '{"error":"token required"}', {"www-authenticate": "Bearer"}, "application/json"
                )
            else:
                await reply(303, "", {"location": "/login"})
            return
        if principal == AGENT and not path.startswith(AGENT_PATHS):
            await reply(403, '{"error":"the agent token cannot use this endpoint"}', None, "application/json")
            return
        if via == "session" and method not in ("GET", "HEAD", "OPTIONS"):
            if not origin_matches_host(headers.get("origin"), headers.get("host")) or headers.get(
                "sec-fetch-site", "same-origin"
            ) not in ("same-origin", "none"):
                await reply(403, "cross-origin request refused")
                return
            if not self.sessions.csrf_ok(sid or "", headers.get(CSRF_HEADER)):
                await reply(403, "missing or invalid CSRF token")
                return
        if method not in ("GET", "HEAD", "OPTIONS") and path == "/login":
            if not origin_matches_host(headers.get("origin"), headers.get("host")):
                await reply(403, "cross-origin request refused")
                return

        state = scope.setdefault("state", {})
        state["principal"], state["via"], state["sid"] = principal, via, sid

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message["headers"]}
                message["headers"] = list(message["headers"]) + [
                    (k.encode(), v.encode()) for k, v in SECURITY_HEADERS.items() if k.encode() not in present
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)
