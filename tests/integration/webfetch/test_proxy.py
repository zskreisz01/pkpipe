"""Spec 008 FR-001/FR-002: the filtering proxy on real sockets against the local fixture site: allowed
loopback port passes, every other private target (also behind a redirect) is refused, the byte budget cuts
oversized responses. httpx stands in for the browser here."""

from __future__ import annotations

import httpx
import pytest

from pkpipe.security.urlfilter import UrlPolicy
from pkpipe.webfetch.proxy import FilteringProxy
from tests.support.fixture_site import fixture_site


@pytest.fixture
def site():
    with fixture_site() as s:
        yield s


def _get(proxy: FilteringProxy, url: str, follow: bool = False) -> httpx.Response:
    with httpx.Client(proxy=proxy.url, timeout=10, follow_redirects=follow, trust_env=False) as c:
        return c.get(url)


def test_allowed_fixture_port_is_proxied(site):
    with FilteringProxy(UrlPolicy(test_loopback=site.allowance), budget_bytes=10**6) as proxy:
        r = _get(proxy, site.url("/clean"))
    assert r.status_code == 200 and "vectorlite" in r.text
    assert proxy.record.blocked == [] and proxy.record.requests == 1 and proxy.record.bytes_in > len(r.text)


def test_without_the_allowance_the_fixture_is_private(site):
    """The test allowance is required: by default loopback is blocked like any private address."""
    with FilteringProxy(UrlPolicy(), budget_bytes=10**6) as proxy:
        r = _get(proxy, site.url("/clean"))
    assert r.status_code == 403 and r.headers["x-pkpipe-blocked"] == "private_network"
    assert site.hits == []  # the request never reached the server
    assert proxy.record.blocked == [{"host": "127.0.0.1", "port": site.port, "reason": "private_network"}]


@pytest.mark.parametrize(
    ("path", "host"),
    [("/redirect-private", "169.254.169.254"), ("/redirect-private-ip", "192.168.1.1")],
)
def test_redirect_to_a_private_address_is_blocked_on_the_hop(site, path, host):
    with FilteringProxy(UrlPolicy(test_loopback=site.allowance), budget_bytes=10**6) as proxy:
        r = _get(proxy, site.url(path), follow=True)
    assert r.status_code == 403
    assert proxy.record.blocked == [{"host": host, "port": 80, "reason": "private_network"}]


def test_redirect_to_another_loopback_port_is_blocked(site):
    """The allowance is exact: the second fixture server on another port stays unreachable."""
    with FilteringProxy(UrlPolicy(test_loopback=site.allowance), budget_bytes=10**6) as proxy:
        r = _get(proxy, site.url("/redirect-other-port"), follow=True)
    assert r.status_code == 403 and proxy.record.blocked[0]["reason"] == "private_network"


def test_connect_tunnel_to_private_address_is_refused(site):
    with FilteringProxy(UrlPolicy(test_loopback=site.allowance), budget_bytes=10**6) as proxy:
        with pytest.raises(httpx.ProxyError):
            _get(proxy, "https://10.0.0.1/")
    assert proxy.record.blocked == [{"host": "10.0.0.1", "port": 443, "reason": "private_network"}]


def test_deny_listed_host_is_refused_before_dns(site):
    def no_dns(host):
        raise AssertionError("deny-list must be checked before resolving")

    policy = UrlPolicy(deny=frozenset({"evil.example"}))
    with FilteringProxy(policy, budget_bytes=10**6, resolve=no_dns) as proxy:
        r = _get(proxy, "http://cdn.evil.example/x")
    assert r.status_code == 403 and r.headers["x-pkpipe-blocked"] == "deny_list"


def test_budget_cuts_an_oversized_response(site):
    with FilteringProxy(UrlPolicy(test_loopback=site.allowance), budget_bytes=100_000) as proxy:
        try:
            r = _get(proxy, site.url("/big"))
            assert len(r.content) < 3_000_000  # truncated body
        except httpx.HTTPError:
            pass  # or the client notices the cut connection
    assert proxy.record.too_large and proxy.record.bytes_in <= 100_000 + 64 * 1024


def test_stop_does_not_wait_for_hanging_connections(site):
    """A browser killed mid-request (timeout) and a stalled upstream (the fixture's /slow page) must not keep
    the proxy alive: every fetch stops its proxy at once, without a thread left behind."""
    import socket
    import threading
    import time

    proxy = FilteringProxy(UrlPolicy(test_loopback=site.allowance), budget_bytes=10**6)
    proxy.start()
    half = socket.create_connection(("127.0.0.1", proxy.port))
    half.sendall(b"GET http://exa")  # an unfinished request line
    stalled = threading.Thread(target=lambda: _quietly(proxy, site.url("/slow")), daemon=True)
    stalled.start()
    time.sleep(0.5)
    started = time.monotonic()
    proxy.stop()
    assert time.monotonic() - started < 3 and not proxy._thread.is_alive()
    half.close()


def _quietly(proxy, url):
    try:
        _get(proxy, url)
    except httpx.HTTPError:
        pass
