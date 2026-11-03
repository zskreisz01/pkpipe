"""Search service (spec 017 FR-003..FR-005): validation, filtering and shaping around a backend.

Search results are untrusted external text. This layer keeps them small and plain, but it does not make them
trusted: the engine datamarks tool results, and a session with this tool never sees raw page text.

* the query is plain text, at most ``MAX_QUERY`` characters, without control characters;
* a result whose URL is not http(s), has credentials, is on the deny-list or points at a private host is
  dropped (no DNS lookup here: the fetch path does the full check);
* titles and snippets lose HTML tags, invisible characters and control characters and are length-capped;
* at most ``MAX_RESULTS`` results, one per URL;
* a failing backend gives ``{"results": [], "error": "<code>"}``, never an exception text.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from urllib.parse import urlsplit

from pkpipe.security.sanitize import strip_invisible
from pkpipe.security.urlfilter import UrlPolicy
from pkpipe.websearch.backends import BackendError, SearchBackend

MAX_QUERY = 400
MAX_RESULTS = 10
MAX_TITLE = 200
MAX_SNIPPET = 500
MAX_URL = 2048

_TAGS = re.compile(r"<[^>]*>")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SPACE = re.compile(r"\s+")


def _no_dns(host: str) -> list[str]:
    """Stand-in resolver: only literals, local names and the deny-list are judged here."""
    return ["93.184.216.34"]


def clean_text(value: object, limit: int) -> str:
    text = value if isinstance(value, str) else ""
    text = html.unescape(_TAGS.sub(" ", text))
    text = strip_invisible(text)[0]
    text = _SPACE.sub(" ", _CONTROL.sub(" ", text)).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def clean_query(query: object) -> str | None:
    if not isinstance(query, str):
        return None
    text = _SPACE.sub(" ", _CONTROL.sub(" ", strip_invisible(query)[0])).strip()
    return text if 0 < len(text) <= MAX_QUERY else None


class SearchService:
    def __init__(self, backend: SearchBackend, load_policy: Callable[[], UrlPolicy], on_event=None):
        self._backend = backend
        self._load_policy = load_policy
        self._on_event = on_event or (lambda **_: None)

    def search(self, query: str, max_results: int = 5) -> dict:
        q = clean_query(query)
        if q is None:
            return {"results": [], "error": "invalid_query"}
        limit = max(1, min(int(max_results or 5), MAX_RESULTS))
        try:
            rows = self._backend.search(q, limit)
        except BackendError as exc:
            self._on_event(outcome="error", reason=exc.code, results=0)
            return {"results": [], "error": exc.code}
        policy = self._load_policy()
        out: list[dict] = []
        seen: set[str] = set()
        dropped = 0
        for row in rows:
            url = row.get("url")
            if not isinstance(url, str) or len(url) > MAX_URL or url in seen or not _public_http(url, policy):
                dropped += 1
                continue
            seen.add(url)
            out.append(
                {
                    "title": clean_text(row.get("title"), MAX_TITLE),
                    "url": url,
                    "snippet": clean_text(row.get("snippet"), MAX_SNIPPET),
                }
            )
            if len(out) == limit:
                break
        self._on_event(outcome="ok", reason="", results=len(out), dropped=dropped)
        return {"results": out}


def _public_http(url: str, policy: UrlPolicy) -> bool:
    if urlsplit(url).scheme.lower() not in ("http", "https"):
        return False
    return policy.check_url(url, _no_dns).allowed
