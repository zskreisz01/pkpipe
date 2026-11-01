"""Spec 008 FR-002 / SC-002 live: real Lightpanda and real Playwright + Chromium behind the filtering proxy,
against the local fixture site (synthetic pages, SSRF attempts, oversized and non-HTML content)."""

from __future__ import annotations

import pytest

from pkpipe.security.urlfilter import UrlPolicy
from pkpipe.webfetch.browsers import ChromiumBrowser, LightpandaBrowser
from pkpipe.webfetch.service import SecureFetcher
from tests.support.fixture_site import fixture_site


@pytest.fixture(scope="module")
def site():
    with fixture_site() as s:
        yield s


class Missing:
    """A primary browser that is not installed: forces the fallback."""

    name = "lightpanda"

    def load(self, url, **kw):
        return LightpandaBrowser(binary="/nonexistent/lightpanda").load(url, **kw)


def lightpanda_only(site, **kw):
    policy = UrlPolicy(test_loopback=site.allowance, **kw)
    return SecureFetcher(policy, primary=LightpandaBrowser(), fallback=None, timeout_s=8)


def chromium_only(site, **kw):
    policy = UrlPolicy(test_loopback=site.allowance, **kw)
    return SecureFetcher(policy, primary=ChromiumBrowser(), fallback=None, timeout_s=15)


BACKENDS = [
    pytest.param(lightpanda_only, marks=pytest.mark.live("lightpanda"), id="lightpanda"),
    pytest.param(chromium_only, marks=pytest.mark.live("browser"), id="chromium"),
]


@pytest.mark.parametrize("make", BACKENDS)
def test_clean_page_is_rendered_with_javascript(site, make):
    res = make(site).fetch(site.url("/clean"))
    assert res["status"] == "ok" and res["kind"] == "html" and res["http_status"] == 200
    assert "vectorlite" in res["html"] and "Rendered by JavaScript: 10 ms queries." in res["html"]


@pytest.mark.parametrize("make", BACKENDS)
def test_redirect_within_the_allowance_is_followed(site, make):
    res = make(site).fetch(site.url("/redirect-ok"))
    assert res["status"] == "ok" and res["final_url"].endswith("/clean")


@pytest.mark.parametrize("make", BACKENDS)
@pytest.mark.parametrize("path", ["/redirect-private", "/redirect-private-ip", "/redirect-other-port"])
def test_redirect_to_a_private_address_is_blocked(site, make, path):
    """SSRF through a redirect: the proxy refuses the hop, the fetch is ``blocked`` (security flag)."""
    res = make(site).fetch(site.url(path))
    assert res["status"] == "blocked" and res["reason"] == "private_network"


@pytest.mark.parametrize("make", BACKENDS)
def test_page_that_calls_private_addresses_is_blocked(site, make):
    """SSRF from page JavaScript (fetch to 10.x and the metadata service) is refused and reported."""
    res = make(site).fetch(site.url("/subrequest-private"))
    assert res["status"] == "blocked" and res["reason"] == "private_network"


@pytest.mark.parametrize("make", BACKENDS)
def test_oversized_page_is_blocked(site, make):
    res = make(site, max_bytes=500_000).fetch(site.url("/big"))
    assert res["status"] == "blocked" and res["reason"] == "size_limit"


@pytest.mark.live("lightpanda")
def test_pdf_is_metadata_only(site):
    res = lightpanda_only(site).fetch(site.url("/doc.pdf"))
    assert res["status"] == "ok" and res["kind"] == "pdf" and res["html"] is None


@pytest.mark.parametrize("make", BACKENDS)
def test_http_error_is_a_failure(site, make):
    res = make(site).fetch(site.url("/error"))
    assert res["status"] == "failed" and res["reason"] == "http_500"


@pytest.mark.live("lightpanda")
def test_without_the_allowance_the_fixture_is_unreachable(site):
    res = SecureFetcher(UrlPolicy(), primary=LightpandaBrowser(), fallback=None).fetch(site.url("/clean"))
    assert res["status"] == "blocked" and res["reason"] == "private_network"


@pytest.mark.live("browser")
def test_lightpanda_failure_falls_back_to_chromium_and_is_counted(site):
    """FR-002 fallback + SC-002: the fallback is used once and the fallback rate is measured."""
    f = SecureFetcher(
        UrlPolicy(test_loopback=site.allowance), primary=Missing(), fallback=ChromiumBrowser(), timeout_s=15
    )
    res = f.fetch(site.url("/clean"))
    assert res["status"] == "ok" and res["fetcher"] == "chromium" and res["fallback_used"]
    assert res["fallback_reason"] == "not_installed"
    assert "Rendered by JavaScript" in res["html"]
    assert f.stats_summary()["fallback_rate"] == 1.0


@pytest.mark.live("lightpanda", "browser")
def test_timeout_falls_back_once_then_fails(site):
    """Edge case: Lightpanda times out, Chromium is tried once and times out too -> failed (no flag)."""
    f = SecureFetcher(
        UrlPolicy(test_loopback=site.allowance),
        primary=LightpandaBrowser(),
        fallback=ChromiumBrowser(),
        timeout_s=2,
    )
    res = f.fetch(site.url("/slow"))
    assert res["status"] == "failed" and res["fallback_used"] and res["fetcher"] is None
