"""Fakes of the web tools of the category sessions: ``webfetch:fetch_page`` (spec 008), ``websearch:search``.

Both record their calls. ``FakeWeb`` serves HTML pages by URL in the shape of the web-fetch MCP result
(:class:`pkpipe.security.verdict.FetchResult`), so the real secure-fetch steps (URL filter, sanitizer,
heuristics, classifier, Reader, verdict) run on them. Pass ``resolve`` as the ``resolver`` port so a flow test
needs no DNS.
"""

from __future__ import annotations

from typing import Any


class FakeWeb:
    def __init__(self, pages: dict[str, str] | None = None, *, down: bool = False):
        self.pages = dict(pages or {})
        self.down = down
        self.fetched: list[str] = []

    def resolve(self, host: str) -> list[str]:
        """The ``resolver`` port: every host of a served page resolves to a public documentation address."""
        known = {url.split("/")[2].split(":")[0] for url in self.pages if "//" in url}
        return ["93.184.216.34"] if host in known else []

    def fetch_page(self, url: str, max_bytes: int | None = None) -> dict[str, Any]:
        self.fetched.append(url)
        if self.down:
            raise ConnectionError("web-fetch server is down")
        html = self.pages.get(url)
        if html is None:
            return {"status": "failed", "url": url, "http_status": 404, "reason": "http_404"}
        return {
            "status": "ok",
            "url": url,
            "final_url": url,
            "http_status": 200,
            "content_type": "text/html",
            "kind": "html",
            "html": html,
            "bytes": len(html.encode("utf-8")),
            "fetcher": "lightpanda",
        }


class FakeSearch:
    """``websearch:search``: canned results; the queries are recorded."""

    def __init__(self, results: list[dict[str, str]] | None = None):
        self.results = (
            results
            if results is not None
            else [
                {
                    "title": "Example result",
                    "url": "https://example.org/result",
                    "snippet": "A synthetic result.",
                }
            ]
        )
        self.queries: list[str] = []

    def search(self, query: str, max_results: int = 5) -> dict[str, Any]:
        self.queries.append(query)
        return {"results": self.results[:max_results]}
