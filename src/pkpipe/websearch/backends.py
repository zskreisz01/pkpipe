"""Search backends (spec 017 FR-002): a backend turns a query into raw ``{title, url, snippet}`` rows.

Swapping the provider is a server option; the tool contract (``search(query, max_results)``) and the flows do
not change. A backend raises ``BackendError`` and nothing else, with a short code and never the provider's
response text.
"""

from __future__ import annotations

from typing import Protocol

import httpx


class BackendError(Exception):
    """The provider could not answer. ``code`` is a stable short string (``timeout``, ``http_503``, ...)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SearchBackend(Protocol):
    name: str

    def search(self, query: str, limit: int) -> list[dict]: ...


def _get_json(client: httpx.Client, url: str, **kwargs) -> dict:
    try:
        resp = client.get(url, **kwargs)
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        raise BackendError("timeout") from None
    except httpx.HTTPStatusError as exc:
        raise BackendError(f"http_{exc.response.status_code}") from None
    except (httpx.HTTPError, ValueError):
        raise BackendError("unavailable") from None
    if not isinstance(data, dict):
        raise BackendError("bad_response")
    return data


class SearxngBackend:
    """SearXNG JSON API. The instance must list ``json`` under ``search.formats`` (``deploy/searxng``)."""

    name = "searxng"

    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self._base = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=15.0)

    def search(self, query: str, limit: int) -> list[dict]:
        data = _get_json(self._client, f"{self._base}/search", params={"q": query, "format": "json"})
        rows = data.get("results")
        if not isinstance(rows, list):
            raise BackendError("bad_response")
        return [
            {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
            for r in rows[: limit * 3]  # the service drops filtered rows, so read a few extra
            if isinstance(r, dict)
        ]


class BraveBackend:
    """Brave Search API (``X-Subscription-Token``)."""

    name = "brave"
    URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, client: httpx.Client | None = None, url: str = URL):
        if not api_key:
            raise ValueError("the brave backend needs an API key (BRAVE_API_KEY)")
        self._key = api_key
        self._url = url
        self._client = client or httpx.Client(timeout=15.0)

    def search(self, query: str, limit: int) -> list[dict]:
        data = _get_json(
            self._client,
            self._url,
            params={"q": query, "count": min(limit * 2, 20)},
            headers={"X-Subscription-Token": self._key, "Accept": "application/json"},
        )
        rows = (data.get("web") or {}).get("results")
        if not isinstance(rows, list):
            raise BackendError("bad_response")
        return [
            {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("description")}
            for r in rows
            if isinstance(r, dict)
        ]
