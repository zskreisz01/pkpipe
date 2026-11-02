"""Query guard (spec 010 FR-002): no web query carries a name that must stay private.

A session with tools proposes a call; before the call leaves the machine to an ``egress`` MCP server (web
search), code checks every string argument against the blocked terms: client and project names the owner
keeps in ``security.query_guard`` (``blocked_terms``, or one term per line in ``terms_file``, which
the context refresh of spec 011 may maintain). A hit refuses the call; the model only sees that it was
blocked, never why.

Matching is case, accent and punctuation insensitive on whole words, so ``Project-Falcon`` and
``project   falcon`` both hit the term ``Project Falcon``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

REASON = "blocked_term"
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def normalise(text: str) -> str:
    folded = unicodedata.normalize("NFKD", str(text)).casefold()
    folded = "".join(c for c in folded if not unicodedata.combining(c) and unicodedata.category(c) != "Cf")
    return " " + _NON_WORD.sub(" ", folded).strip() + " "


def parse_terms(text: str | None) -> list[str]:
    """One term per line; blank lines and ``#`` comments are ignored."""
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def blocked_terms(config: Mapping[str, Any] | None, terms_text: str | None = None) -> list[str]:
    cfg = config or {}
    terms = [str(t) for t in cfg.get("blocked_terms") or []] + parse_terms(terms_text)
    return [t for t in dict.fromkeys(terms) if normalise(t).strip()]


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _strings(v)


def check_arguments(arguments: Any, terms: Iterable[str]) -> str | None:
    """:data:`REASON` when any string argument contains a blocked term, else None (never the term)."""
    needles = [n for n in (normalise(t) for t in terms) if n.strip()]
    for text in _strings(arguments):
        hay = normalise(text)
        if any(n in hay for n in needles):
            return REASON
    return None
