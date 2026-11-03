"""Spec 017: query validation, result filtering and shaping, backend errors (fake backend, no network)."""

from __future__ import annotations

import httpx
import pytest

from pkpipe.security.urlfilter import UrlPolicy
from pkpipe.websearch.backends import BackendError, BraveBackend, SearxngBackend
from pkpipe.websearch.service import MAX_QUERY, MAX_SNIPPET, SearchService, clean_query

POLICY = UrlPolicy.from_config({}, "denied.example\n")


class FakeBackend:
    name = "fake"

    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.queries = rows or [], error, []

    def search(self, query, limit):
        self.queries.append((query, limit))
        if self.error:
            raise BackendError(self.error)
        return self.rows


def svc(rows=None, error=None):
    backend = FakeBackend(rows, error)
    return SearchService(backend, lambda: POLICY), backend


def row(url, title="t", snippet="s"):
    return {"title": title, "url": url, "snippet": snippet}


def test_results_are_shaped_and_capped():
    service, backend = svc([row(f"https://example.org/{i}") for i in range(30)])
    out = service.search("q", 99)
    assert len(out["results"]) == 10 and backend.queries == [("q", 10)]
    assert set(out["results"][0]) == {"title", "url", "snippet"}


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "http://user:pw@example.org/",
        "http://169.254.169.254/latest",
        "http://localhost:8080/",
        "http://10.0.0.5/",
        "https://denied.example/page",
        "https://sub.denied.example/page",
        "",
        None,
    ],
)
def test_unsafe_urls_are_dropped(url):
    service, _ = svc([row(url), row("https://example.org/ok")])
    assert [r["url"] for r in service.search("q")["results"]] == ["https://example.org/ok"]


def test_duplicate_urls_are_listed_once():
    service, _ = svc([row("https://example.org/a"), row("https://example.org/a")])
    assert len(service.search("q")["results"]) == 1


def test_text_is_plain_and_bounded():
    dirty = "<b>Bold</b> &amp; plain​ text\x07 " + "x" * 2000
    service, _ = svc([row("https://example.org/a", title="<script>x</script>T", snippet=dirty)])
    r = service.search("q")["results"][0]
    assert "<" not in r["snippet"] and "​" not in r["snippet"] and "\x07" not in r["snippet"]
    assert r["snippet"].startswith("Bold & plain text") and len(r["snippet"]) <= MAX_SNIPPET
    assert r["title"] == "x T" or r["title"].endswith("T")


@pytest.mark.parametrize("query", ["", "   ", "\x00\x01", "a" * (MAX_QUERY + 1), None, 5])
def test_invalid_queries_never_reach_the_backend(query):
    service, backend = svc([row("https://example.org/a")])
    assert service.search(query) == {"results": [], "error": "invalid_query"}
    assert backend.queries == []


def test_query_is_normalised():
    assert clean_query("  hello​ \n world\x07 ") == "hello world"


def test_backend_error_is_a_code_not_a_message():
    service, _ = svc(error="timeout")
    assert service.search("q") == {"results": [], "error": "timeout"}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_searxng_backend_maps_rows_and_sends_json_format():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"results": [{"title": "T", "url": "https://e.org", "content": "C"}]})

    rows = SearxngBackend("http://sx:8080/", _client(handler)).search("my query", 5)
    assert rows == [{"title": "T", "url": "https://e.org", "snippet": "C"}]
    assert "format=json" in seen["url"] and "q=my+query" in seen["url"]


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(503, text="secret provider text"), "http_503"),
        (httpx.Response(200, text="<html>not json</html>"), "unavailable"),
        (httpx.Response(200, json=["x"]), "bad_response"),
        (httpx.Response(200, json={"results": "x"}), "bad_response"),
    ],
)
def test_searxng_failures_give_stable_codes(response, code):
    with pytest.raises(BackendError) as err:
        SearxngBackend("http://sx", _client(lambda r: response)).search("q", 3)
    assert err.value.code == code and "secret" not in str(err.value)


def test_brave_backend_sends_the_key_and_maps_rows():
    seen = {}

    def handler(request):
        seen["token"] = request.headers["X-Subscription-Token"]
        return httpx.Response(
            200, json={"web": {"results": [{"title": "T", "url": "https://e.org", "description": "D"}]}}
        )

    rows = BraveBackend("k123", _client(handler)).search("q", 3)
    assert rows == [{"title": "T", "url": "https://e.org", "snippet": "D"}] and seen["token"] == "k123"


def test_brave_needs_a_key():
    with pytest.raises(ValueError):
        BraveBackend("")
