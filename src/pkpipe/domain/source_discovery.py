"""Source discovery for items that name no URL (spec 025): the search query and the choice of candidates.

Pure functions. The query is built only from fields the pipeline already validated (the image description's
``topic`` and ``visible_text``, or the text of the owner's note), by a template with two placeholders,
never by a model: text derived from untrusted pixels or pages must not decide what leaves the machine.
The query is neutralised (no URL, no markdown or HTML opener, no control or zero-width character), cut at
a word boundary and checked by the web-query guard before the search tool is called. The search results are
untrusted data: only their URLs are used, after the URL policy.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from pkpipe.domain.text_safety import PLACEHOLDER, neutralize_text

_TEXT_CHARS = 400
_SPACES = re.compile(r"\s+")
_EDGES = " \t\r\n-–—_:;,.|/\\*#~^"


def _clean(text: object, limit: int) -> str:
    """One line of plain words: ``neutralize_text`` (URLs, markup openers), the URL placeholder removed."""
    out = neutralize_text(text, limit=limit).replace(PLACEHOLDER, " ")
    out = "".join(c if c.isprintable() else " " for c in out)
    return _SPACES.sub(" ", out).strip(_EDGES)


def build_query(template: str, topic: object, text: object, max_chars: int) -> str:
    """The search query: ``template`` with ``{topic}`` and ``{text}`` filled from cleaned values, at most
    ``max_chars`` characters, cut after a whole word. Empty when nothing searchable is left (no letter or
    digit)."""
    query = _SPACES.sub(
        " ", template.format(topic=_clean(topic, _TEXT_CHARS), text=_clean(text, _TEXT_CHARS))
    )
    query = query.strip(_EDGES)
    if len(query) > max_chars:
        cut = query[:max_chars]
        if not query[max_chars].isspace():
            cut = cut.rsplit(" ", 1)[0] if " " in cut else cut
        query = cut.strip(_EDGES)
    return query if re.search(r"[^\W_]", query) else ""


def pick_candidates(
    results: object, allowed: Callable[[str], bool], limit: int
) -> tuple[list[str], dict[str, int]]:
    """The URLs to fetch, best result first: http(s) only, no credentials, each page once, at most one page
    per host (a second page of the same site rarely adds a different source), passing the URL policy.

    Returns the URLs and a content-free tally (``results``, ``dropped``) for the span and the report."""
    rows = results.get("results") if isinstance(results, dict) else None
    rows = rows if isinstance(rows, list) else []
    picked: list[str] = []
    seen_hosts: set[str] = set()
    seen_urls: set[str] = set()
    for row in rows:
        url = row.get("url") if isinstance(row, dict) else None
        if not isinstance(url, str):
            continue
        url = url.strip()
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        key = url.split("#", 1)[0].rstrip("/")
        if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
            continue
        if host in seen_hosts or key in seen_urls or not allowed(url):
            continue
        seen_hosts.add(host)
        seen_urls.add(key)
        picked.append(url)
        if len(picked) >= limit:
            break
    return picked, {"results": len(rows), "dropped": len(rows) - len(picked)}


def failure_reason(code: str) -> str:
    """The owner-facing reason in the processing list (fixed texts: nothing of the item or the query)."""
    return {
        "no_description": "no source found: the image has no cached description; triage it again",
        "nothing_to_search": "no source found: nothing searchable in the item; add a link to its entry",
        "query_blocked": "no source found: the search query was refused by the web query guard",
        "no_results": "no source found by web search; add a link to the entry and release it again",
        "search_failed": "no source found: the web search failed; the next run tries again",
    }.get(code, "no source found")


def discovered_fields(urls: list[str]) -> dict[str, Any]:
    """The item fields after a successful search: the first URL is fetched, the rest wait."""
    return {"source_url": urls[0], "source_candidates": urls[1:], "source_discovered": True}
