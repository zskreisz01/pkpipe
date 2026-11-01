"""Spec 008 FR-002 / SC-002: Lightpanda first, one Chromium fallback on error, blocks never retried, outcomes
counted (fallback rate). Browsers are fakes here; the real ones run in tests/integration/webfetch."""

from __future__ import annotations

import json

from pkpipe.security.urlfilter import UrlPolicy
from pkpipe.webfetch.browsers import BrowserError, Page
from pkpipe.webfetch.service import SecureFetcher, content_kind

PUBLIC = lambda host: ["93.184.215.14"]  # noqa: E731
URL = "https://example.org/a"


class FakeBrowser:
    def __init__(self, name, result=None, error=None):
        self.name, self.result, self.error = name, result, error
        self.calls: list[dict] = []

    def load(self, url, *, proxy_url, max_bytes, timeout_s):
        self.calls.append({"url": url, "proxy": proxy_url, "max_bytes": max_bytes, "timeout_s": timeout_s})
        if self.error:
            raise BrowserError(self.error)
        return self.result or Page(url, url, 200, {"content-type": "text/html"}, "<html><p>ok</p></html>")


def fetcher(primary, fallback, **kw):
    return SecureFetcher(UrlPolicy(**kw), primary=primary, fallback=fallback, resolve=PUBLIC, timeout_s=5)


def test_lightpanda_success_needs_no_fallback():
    lp, cr = FakeBrowser("lightpanda"), FakeBrowser("chromium")
    res = fetcher(lp, cr).fetch(URL)
    assert res["status"] == "ok" and res["fetcher"] == "lightpanda" and not res["fallback_used"]
    assert res["kind"] == "html" and "<p>ok</p>" in res["html"] and cr.calls == []
    assert lp.calls[0]["proxy"].startswith("http://127.0.0.1:")  # always behind the filtering proxy


def test_error_falls_back_to_chromium_once():
    lp, cr = FakeBrowser("lightpanda", error="navigation_error"), FakeBrowser("chromium")
    f = fetcher(lp, cr)
    res = f.fetch(URL)
    assert res["status"] == "ok" and res["fetcher"] == "chromium"
    assert res["fallback_used"] and res["fallback_reason"] == "navigation_error"
    assert len(lp.calls) == len(cr.calls) == 1
    assert f.stats_summary()["fallback_rate"] == 1.0


def test_timeout_then_fallback_failure_is_failed():
    """Edge case: timeout -> fallback once -> failed state (not blocked, so not a security flag)."""
    lp, cr = FakeBrowser("lightpanda", error="timeout"), FakeBrowser("chromium", error="timeout")
    res = fetcher(lp, cr).fetch(URL)
    assert res["status"] == "failed" and res["reason"] == "timeout" and res["fallback_used"]
    assert len(cr.calls) == 1


def test_blocked_url_never_reaches_a_browser():
    lp, cr = FakeBrowser("lightpanda"), FakeBrowser("chromium")
    res = fetcher(lp, cr).fetch("http://10.0.0.1/")
    assert res == {
        "url": "http://10.0.0.1/",
        "fallback_used": False,
        "status": "blocked",
        "reason": "private_network",
    }
    assert lp.calls == [] and cr.calls == []


def test_too_large_is_blocked_without_fallback():
    """An oversized page is a size-limit block; Chromium is not tried (it would hit the same limit). Private
    sub-requests are covered with the real proxy in tests/integration/webfetch."""
    lp = FakeBrowser("lightpanda", error="too_large")
    cr = FakeBrowser("chromium")
    res = fetcher(lp, cr).fetch(URL)
    assert res["status"] == "blocked" and res["reason"] == "size_limit" and cr.calls == []


def test_http_error_is_failed_and_non_html_is_metadata_only():
    lp = FakeBrowser("lightpanda", result=Page(URL, URL, 503, {"content-type": "text/html"}, "x"))
    assert fetcher(lp, None).fetch(URL)["reason"] == "http_503"
    pdf = Page(
        URL, URL + ".pdf", 200, {"content-type": "application/pdf", "content-length": "1234"}, "<html/>"
    )
    res = fetcher(FakeBrowser("lightpanda", result=pdf), None).fetch(URL)
    assert res["status"] == "ok" and res["kind"] == "pdf" and res["html"] is None and res["bytes"] == 1234


def test_rendered_document_over_the_limit_is_blocked():
    big = Page(URL, URL, 200, {"content-type": "text/html"}, "x" * 2000)
    res = fetcher(FakeBrowser("lightpanda", result=big), None, max_bytes=1000).fetch(URL)
    assert res["status"] == "blocked" and res["reason"] == "size_limit"


def test_caller_can_only_tighten_the_size_limit():
    lp = FakeBrowser("lightpanda")
    f = fetcher(lp, None, max_bytes=1000)
    f.fetch(URL, max_bytes=10_000)
    f.fetch(URL, max_bytes=500)
    assert [c["max_bytes"] for c in lp.calls] == [1000, 500]


def test_stats_file_has_one_line_per_fetch_without_urls(tmp_path):
    stats = tmp_path / "stats.jsonl"
    f = SecureFetcher(
        UrlPolicy(),
        primary=FakeBrowser("lightpanda", error="timeout"),
        fallback=FakeBrowser("chromium"),
        resolve=PUBLIC,
        stats_file=stats,
    )
    f.fetch(URL)
    f.fetch("http://192.168.0.1/")
    lines = [json.loads(x) for x in stats.read_text().splitlines()]
    assert [(x["status"], x["fetcher"], x["fallback_used"]) for x in lines] == [
        ("ok", "chromium", True),
        ("blocked", None, False),
    ]
    assert "example.org" not in stats.read_text() and "192.168" not in stats.read_text()
    assert f.stats_summary() == {
        "fetches": 2,
        "status.ok": 1,
        "fetcher.chromium": 1,
        "fallback": 1,
        "status.blocked": 1,
        "fallback_rate": 0.5,
    }


def test_content_kind():
    assert [
        content_kind(c) for c in ("text/html; charset=utf-8", None, "text/plain", "video/mp4", "x/y")
    ] == [
        "html",
        "html",
        "text",
        "video",
        "other",
    ]
