"""Classified failures of a learning backend (spec 032).

A browser automation client reports problems as free text. The owner needs to know which kind of problem
it is, because the answer differs: wait for the quota, log in again, report a changed page, or just try
again. The classification looks only at the short message of the client (never at page text) and is pure.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

QUOTA = "quota"
AUTH = "auth"
PAGE_CHANGED = "page_changed"
TRANSIENT = "transient"
UNKNOWN = "unknown"

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        QUOTA,
        re.compile(
            r"quota|limit reached|limit exceeded|rate.?limit|too many requests|\b429\b|usage limit|"
            r"daily limit|exceeded .*limit|try again tomorrow",
            re.I,
        ),
    ),
    (
        AUTH,
        re.compile(
            r"not logged in|log ?in required|nlm login|session expired|unauthori[sz]ed|\b401\b|\b403\b|"
            r"authentication|re-?authenticate|cookies? (?:have )?expired",
            re.I,
        ),
    ),
    (
        PAGE_CHANGED,
        re.compile(r"selector|element not found|no such element|did not report|page may have changed", re.I),
    ),
    (
        TRANSIENT,
        re.compile(
            r"timed out|timeout|temporar|connection (?:reset|refused|aborted)|network|\b50[234]\b|"
            r"service unavailable|try again",
            re.I,
        ),
    ),
)


def classify_failure(message: str) -> str:
    """``quota``, ``auth``, ``page_changed``, ``transient`` or ``unknown`` for a client's short message."""
    for kind, pattern in _RULES:
        if pattern.search(message or ""):
            return kind
    return UNKNOWN


def quota_wait(usage: dict | None, *, min_remaining_percent: float = 5.0) -> float | None:
    """The epoch second after which the quota allows new work, ``None`` when it allows it now.

    ``usage`` is the JSON of ``nlm usage --json``: ``{"windows": [{"window", "percent_remaining",
    "resets_at"}]}``. Any window with less than ``min_remaining_percent`` left blocks; the wait ends when the
    latest blocking window resets. An unreadable value never blocks (the real call would then fail and be
    classified)."""
    blocked: list[float] = []
    for w in (usage or {}).get("windows") or []:
        try:
            if float(w.get("percent_remaining", 100.0)) >= min_remaining_percent:
                continue
            blocked.append(datetime.fromisoformat(str(w["resets_at"])).astimezone(UTC).timestamp())
        except (KeyError, TypeError, ValueError):
            continue
    return max(blocked) if blocked else None


def until_text(epoch: float | None) -> str:
    """``2026-11-10 11:37 UTC`` for a reason text; ``an unknown time`` without a value."""
    if epoch is None:
        return "an unknown time"
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d %H:%M UTC")


def quota_reset_guess(usage: dict | None) -> float | None:
    """After a quota refusal: the reset of the window with the least quota left (``None`` when unknown)."""
    best: tuple[float, float] | None = None
    for w in (usage or {}).get("windows") or []:
        try:
            left = float(w["percent_remaining"])
            reset = datetime.fromisoformat(str(w["resets_at"])).astimezone(UTC).timestamp()
        except (KeyError, TypeError, ValueError):
            continue
        if best is None or left < best[0]:
            best = (left, reset)
    return best[1] if best else None
