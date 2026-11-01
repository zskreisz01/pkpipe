"""Spec 014 FR-001: bind policy, tokens, sessions, throttle and host allowlist (pure logic)."""

from __future__ import annotations

import json
import os
import stat

import pytest

from pkpipe.api.security import (
    BindRefused,
    LoginThrottle,
    Sessions,
    Tokens,
    allowed_hosts,
    check_bind,
    host_of,
    origin_matches_host,
)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "100.101.102.103", "fd7a:115c:a1e0::1"])
def test_bind_allows_loopback_and_tailscale(host):
    """FR-001: loopback and Tailscale (CGNAT 100.64/10, fd7a:115c:a1e0::/48) addresses only."""
    assert check_bind(host) == host


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "::", "192.168.1.20", "10.0.0.5", "example.com", "100.128.0.1", ""]
)
def test_bind_refuses_everything_else(host):
    """FR-001: wildcard and LAN/public addresses are refused (a token alone is not enough)."""
    with pytest.raises(BindRefused):
        check_bind(host)


def test_tokens_are_generated_once_with_owner_only_permissions(tmp_path):
    """FR-001: the token file is created 0600, stable across restarts, owner and agent tokens differ."""
    first = Tokens.load(tmp_path, {})
    path = tmp_path / "tokens.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert Tokens.load(tmp_path, {}) == first
    assert first.owner != first.agent and len(first.owner) >= 32
    assert set(json.loads(path.read_text())) == {"owner", "agent"}


def test_env_tokens_win_and_weak_or_equal_tokens_are_refused(tmp_path):
    ok = "x" * 30
    assert Tokens.load(tmp_path, {"PKPIPE_TOKEN": ok}).owner == ok
    assert not (tmp_path / "tokens.json").exists()
    with pytest.raises(ValueError, match="at least"):
        Tokens.load(tmp_path, {"PKPIPE_TOKEN": "short"})
    with pytest.raises(ValueError, match="differ"):
        Tokens.load(tmp_path, {"PKPIPE_TOKEN": ok, "PKPIPE_AGENT_TOKEN": ok})


def test_scope_of_distinguishes_owner_agent_and_garbage():
    t = Tokens("o" * 30, "a" * 30)
    assert t.scope_of("o" * 30) == "owner" and t.scope_of("a" * 30) == "agent"
    assert t.scope_of("o" * 29) is None and t.scope_of("") is None and t.scope_of(None) is None
    assert Tokens("o" * 30).scope_of("a" * 30) is None


def test_sessions_expire_and_csrf_is_bound_to_the_session():
    s = Sessions(ttl_s=0)
    sid = s.create()
    assert not s.valid(sid)  # expired immediately
    s = Sessions()
    a, b = s.create(), s.create()
    assert s.valid(a) and not s.valid("nope") and not s.valid(None)
    assert s.csrf_ok(a, s.csrf(a)) and not s.csrf_ok(a, s.csrf(b)) and not s.csrf_ok(a, None)
    s.drop(a)
    assert not s.valid(a)


def test_login_throttle_blocks_after_repeated_failures():
    t = LoginThrottle(limit=3, window_s=60)
    for _ in range(3):
        assert not t.blocked()
        t.failed()
    assert t.blocked()


def test_host_allowlist_and_origin_matching():
    hosts = allowed_hosts("127.0.0.1", extra=["box.tail1234.ts.net"])
    assert host_of("127.0.0.1:8787") in hosts and host_of("LOCALHOST") in hosts
    assert host_of("[::1]:8787") in hosts and host_of("box.tail1234.ts.net") in hosts
    assert host_of("evil.example") not in hosts and host_of("127.0.0.1.evil.example") not in hosts
    assert origin_matches_host("http://127.0.0.1:8787", "127.0.0.1:8787")
    assert not origin_matches_host("http://evil.example", "127.0.0.1:8787")
    assert not origin_matches_host("null", "127.0.0.1:8787")
    assert origin_matches_host(None, "127.0.0.1:8787")
