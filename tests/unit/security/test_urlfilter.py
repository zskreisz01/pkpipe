"""Spec 008 FR-001: URL filter (scheme, deny-list, private networks after DNS resolution, size limit) and the
test-only loopback allowance that no config can enable."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pkpipe.engine.config_models import SecurityConfig
from pkpipe.security.urlfilter import (
    Reason,
    UrlPolicy,
    is_public_address,
    parse_deny_list,
    parse_test_loopback,
)

PUBLIC = lambda host: ["93.184.215.14"]  # noqa: E731 - fake resolver: every name is public


def test_public_https_url_is_allowed():
    d = UrlPolicy().check_url("https://example.org/a?b=1", PUBLIC)
    assert d.allowed and d.addresses == ("93.184.215.14",) and d.port == 443


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8080/",
        "http://10.0.0.5/",
        "http://172.16.3.4/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://100.64.0.1/",  # carrier-grade NAT
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[::ffff:10.0.0.1]/",  # IPv4-mapped private
        "http://0.0.0.0/",
        "http://2130706433/",  # 127.0.0.1 as an integer
        "http://0x7f000001/",
        "http://127.1/",
        "http://router.local/",
        "http://intranet/",
        "http://printer.internal/",
    ],
)
def test_private_network_targets_are_blocked(url):
    """SSRF: loopback, RFC 1918, link-local, CGNAT, IPv6 local and obfuscated forms are all private."""
    d = UrlPolicy().check_url(url, PUBLIC)
    assert not d.allowed and d.reason == Reason.PRIVATE and d.flag


def test_a_public_name_that_resolves_into_the_private_network_is_blocked():
    """DNS can point any name at 10.x: the check runs on every resolved address."""
    d = UrlPolicy().check_url("https://innocent.example.com/", lambda h: ["93.184.215.14", "10.1.2.3"])
    assert not d.allowed and d.reason == Reason.PRIVATE


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("file:///etc/passwd", Reason.SCHEME),
        ("ftp://example.org/x", Reason.SCHEME),
        ("javascript:alert(1)", Reason.SCHEME),
        ("gopher://example.org/", Reason.SCHEME),
        ("https://user:pw@example.org/", Reason.CREDENTIALS),
        ("https://example.org/" + "a" * 3000, Reason.TOO_LONG),
        ("https://exa mple.org/", Reason.MALFORMED),
        ("", Reason.MALFORMED),
        ("https://", Reason.MALFORMED),
    ],
)
def test_bad_urls_are_rejected(url, reason):
    assert UrlPolicy().check_url(url, PUBLIC).reason == reason


def test_unresolvable_host_is_a_failure_not_a_flag():
    def fail(host):
        raise OSError("nodename nor servname provided")

    d = UrlPolicy().check_url("https://no-such-host.example/", fail)
    assert not d.allowed and d.reason == Reason.UNRESOLVABLE and not d.flag


def test_deny_list_matches_domain_and_subdomains_only():
    deny = parse_deny_list("# comment\nevil.example\n*.bad.example.net\nhttps://phish.example/path\n\n")
    assert deny == {"evil.example", "bad.example.net", "phish.example"}
    policy = UrlPolicy(deny=deny)
    assert policy.check_url("https://evil.example/x", PUBLIC).reason == Reason.DENY_LIST
    assert policy.check_url("https://cdn.EVIL.example./x", PUBLIC).reason == Reason.DENY_LIST
    assert policy.check_url("https://bad.example.net/", PUBLIC).reason == Reason.DENY_LIST
    assert policy.check_url("https://notevil.example/", PUBLIC).allowed


def test_size_limit():
    policy = UrlPolicy(max_bytes=1000)
    assert policy.check_size(1000) is None and policy.check_size(1001) == Reason.SIZE


def test_from_config_reads_url_filter_and_deny_list():
    p = UrlPolicy.from_config({"block_private_networks": True, "max_bytes": 5}, "denied.example\n")
    assert p.max_bytes == 5 and p.deny == {"denied.example"} and not p.test_loopback


def test_test_loopback_allowance_is_exact_host_and_port():
    """Only the fixture server's own port: other loopback ports and private addresses stay blocked."""
    policy = UrlPolicy(test_loopback=frozenset({("127.0.0.1", 8123)}))
    assert policy.check_url("http://127.0.0.1:8123/clean", PUBLIC).allowed
    assert policy.check_url("http://127.0.0.1:8124/", PUBLIC).reason == Reason.PRIVATE
    assert policy.check_url("http://localhost:8123/", PUBLIC).reason == Reason.PRIVATE
    assert policy.check_url("http://10.0.0.1:8123/", PUBLIC).reason == Reason.PRIVATE


@pytest.mark.parametrize("bad", ["10.0.0.1:80", "example.org:80", "127.0.0.1", "localhost:80", "127.0.0.1:0"])
def test_test_loopback_env_value_accepts_only_loopback_literals_with_port(bad):
    with pytest.raises(ValueError):
        parse_test_loopback(bad)
    assert parse_test_loopback("127.0.0.1:9000, [::1]:9001") == {("127.0.0.1", 9000), ("::1", 9001)}


def test_pack_config_cannot_enable_the_test_allowance():
    """The allowance has no config key: security.url_filter rejects unknown keys (constitution IV)."""
    base = {
        "prompt_guard": {"model": "m", "threshold": 0.5, "chunk_tokens": 512},
        "output_guard": {"model": "g", "block_on": ["Unsafe"]},
        "canary": {"rotate": "per_run"},
        "quarantine_dir": "q",
    }
    SecurityConfig.model_validate({**base, "url_filter": {"max_bytes": 10}})
    for key in ("test_loopback", "allow_loopback", "allow_hosts"):
        with pytest.raises(ValidationError):
            SecurityConfig.model_validate({**base, "url_filter": {"max_bytes": 10, key: ["127.0.0.1:80"]}})


def test_disabled_private_block_allows_private_targets():
    """An owner may switch the block off explicitly (default on); the deny-list still applies."""
    p = UrlPolicy(block_private_networks=False, deny=frozenset({"evil.example"}))
    assert p.check_url("http://10.0.0.1/", PUBLIC).allowed
    assert not p.check_url("http://evil.example/", PUBLIC).allowed


@pytest.mark.parametrize(
    ("addr", "public"),
    [("8.8.8.8", True), ("2606:4700::1111", True), ("10.0.0.1", False), ("::1", False), ("bogus", False)],
)
def test_is_public_address(addr, public):
    assert is_public_address(addr) is public
