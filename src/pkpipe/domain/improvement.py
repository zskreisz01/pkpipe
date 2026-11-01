"""Self-improvement rules (spec 015): the backlog item, who may change what, and the deterministic checks
around the two model sessions (extraction and weekly review).

The model only *proposes*. Code decides what reaches the backlog: a statement is accepted only when it is
a verbatim passage of the journal section it was extracted from (FR-001, SC-001: no invented items), a piece
of evidence only when its quote occurs verbatim in the journal days it claims to come from, and the status
of an item is never changed by the pipeline except the initial ``proposed`` (constitution V and VI).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, field_validator


class Area(StrEnum):
    PERSONAL = "personal"
    LEARNING = "learning"
    WORK = "work"
    SYSTEM = "system"


class ItemType(StrEnum):
    HABIT = "habit"
    EXPERIMENT = "experiment"
    SYSTEM_CHANGE = "system-change"


class Status(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    REVIEWING = "reviewing"
    KEPT = "kept"
    ADJUSTED = "adjusted"
    DROPPED = "dropped"


class Verdict(StrEnum):
    KEEP = "keep"
    ADJUST = "adjust"
    DROP = "drop"


class Signal(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    NEUTRAL = "neutral"


#: Status changes the *owner* may make (the pipeline makes none: see :func:`check_transition`). ``proposed``
#: is only ever the initial status of a new item, so nothing leads back to it.
TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.PROPOSED: frozenset({Status.PROPOSED, Status.ACTIVE, Status.DROPPED}),
    Status.ACTIVE: frozenset({Status.ACTIVE, Status.REVIEWING, Status.KEPT, Status.ADJUSTED, Status.DROPPED}),
    Status.REVIEWING: frozenset(
        {Status.REVIEWING, Status.ACTIVE, Status.KEPT, Status.ADJUSTED, Status.DROPPED}
    ),
    Status.ADJUSTED: frozenset(
        {Status.ADJUSTED, Status.ACTIVE, Status.REVIEWING, Status.KEPT, Status.DROPPED}
    ),
    Status.KEPT: frozenset({Status.KEPT, Status.REVIEWING, Status.DROPPED}),
    Status.DROPPED: frozenset({Status.DROPPED}),
}

#: Fields the weekly review (pipeline) may write on an existing item; everything else is the owner's.
AGENT_FIELDS = ("evidence", "verdict", "reason", "next_experiment", "proposal")


def check_transition(old: Status, new: Status, *, owner_decided: bool) -> str | None:
    """Why ``old -> new`` is not allowed, or ``None``. FR-001: any change of status needs the owner's
    confirmation; ``owner_decided`` is ``False`` for everything the pipeline does."""
    if new not in TRANSITIONS[old]:
        return f"status change {old.value} -> {new.value} is not allowed"
    if new is not old and not owner_decided:
        return "a status change needs the owner's confirmation"
    return None


# -- text helpers -------------------------------------------------------------------------------------------
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)")
_WS = re.compile(r"\s+")


def clean(text: Any) -> str:
    """One line: whitespace collapsed, a leading list marker removed."""
    return _WS.sub(" ", _LIST_MARKER.sub("", str(text or ""))).strip()


def normalize(text: Any) -> str:
    """Comparison form: list markers (also at the start of every line), whitespace and case ignored."""
    lines = (_LIST_MARKER.sub("", line) for line in str(text or "").splitlines() or [""])
    return _WS.sub(" ", " ".join(lines)).strip().casefold()


def item_id_for(source_note: str, statement: str) -> str:
    """Stable id: hash of the journal note and the owner's wording, so a re-run never duplicates an item."""
    return hashlib.sha256(f"{source_note.strip()}|{normalize(statement)}".encode()).hexdigest()[:12]


def journal_path(folder: str, day: date) -> str:
    return f"{folder.strip().rstrip('/')}/{day.isoformat()}.md"


_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def extract_section(body: str, heading: str = "Improvement") -> str:
    """Text below the journal's ``## Improvement`` heading (any level, singular or plural), up to the next
    heading of the same or a higher level. ``""`` when there is no such section or it is empty."""
    wanted = {heading.strip().casefold(), heading.strip().casefold() + "s"}
    level: int | None = None
    lines: list[str] = []
    in_fence = False
    for line in (body or "").splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        if level is None:
            if m and m.group(2).strip().casefold() in wanted:
                level = len(m.group(1))
            continue
        if m and len(m.group(1)) <= level:
            break
        lines.append(line)
    return "\n".join(lines).strip()


# -- extraction (US1) ---------------------------------------------------------------------------------------
MAX_STATEMENT = 300
MAX_CRITERION = 200


@dataclass
class Grounded:
    items: list[dict[str, Any]]
    dropped: dict[str, int]


def ground_items(
    proposed: list[dict[str, Any]],
    *,
    section: str,
    source_note: str,
    day: date,
    existing_ids: Collection[str] = (),
    max_items: int = 10,
) -> Grounded:
    """Turn the session's proposals into ``proposed`` backlog items. Dropped (and counted): statements that
    are not verbatim in ``section`` (invented or paraphrased), empty or too long ones, unknown area or type,
    duplicates, and items already in the backlog. A success criterion survives only if it is also verbatim
    in the section (the instruction says: only when the owner wrote one)."""
    haystack = normalize(section)
    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    items: list[dict[str, Any]] = []
    seen = set(existing_ids)
    for raw in proposed if isinstance(proposed, list) else []:
        if not isinstance(raw, dict):
            drop("malformed")
            continue
        statement = clean(raw.get("statement"))
        if not statement or len(statement) > MAX_STATEMENT:
            drop("bad_statement")
            continue
        if normalize(statement) not in haystack:
            drop("not_in_journal")
            continue
        try:
            area, kind = (
                Area(str(raw.get("area", "")).strip().lower()),
                ItemType(str(raw.get("type", "")).strip().lower()),
            )
        except ValueError:
            drop("bad_enum")
            continue
        item_id = item_id_for(source_note, statement)
        if item_id in seen:
            drop("duplicate")
            continue
        criterion = clean(raw.get("success_criterion"))
        if criterion and (len(criterion) > MAX_CRITERION or normalize(criterion) not in haystack):
            criterion = ""
        if len(items) >= max_items:
            drop("over_limit")
            continue
        seen.add(item_id)
        items.append(
            {
                "id": item_id,
                "source_note": source_note,
                "date": day.isoformat(),
                "statement": statement,
                "area": area.value,
                "type": kind.value,
                "success_criterion": criterion,
                "status": Status.PROPOSED.value,
            }
        )
    return Grounded(items, dropped)


# -- weekly review (US2) ------------------------------------------------------------------------------------
ACTIVE_STATES = (Status.ACTIVE.value, Status.REVIEWING.value)
JOURNAL_CHARS = 4000
ITEM_JOURNAL_CHARS = 12000


def parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def review_window(rows: list[dict[str, Any]], today: date, max_days: int) -> tuple[date, list[date]]:
    """The journal days the review reads: from the day after the oldest item was written (the item's own day
    holds only the statement, which is not evidence; at most ``max_days`` back) through today."""
    earliest = today - timedelta(days=max(1, max_days) - 1)
    starts = [(d + timedelta(days=1)) if (d := parse_date(r.get("date"))) else earliest for r in rows]
    start = max(min(starts, default=earliest), earliest)
    return start, [start + timedelta(days=n) for n in range((today - start).days + 1)]


def select_review(rows: list[dict[str, Any]], today: date, *, max_days: int = 28) -> dict[str, list[Any]]:
    """Items to review (status active or reviewing) and the journal days to read for them."""
    items = [r for r in rows if r.get("status") in ACTIVE_STATES]
    _, days = review_window(items, today, max_days)
    return {"items": items, "days": days if items else []}


def attach_journals(
    items: list[dict[str, Any]], notes: dict[str, str], today: date, *, max_days: int = 28
) -> list[dict[str, Any]]:
    """Give each item the journal entries after the day it was written (``notes``: ISO day -> body). The text
    per day and per item is capped. Items carry ``journal_days`` (for code) and ``journal`` (the only thing
    the session sees)."""
    out = []
    floor = today - timedelta(days=max(1, max_days) - 1)
    for item in items:
        written = parse_date(item.get("date"))
        start = max(written + timedelta(days=1) if written else floor, floor)
        days = []
        for iso in sorted(notes):
            d = parse_date(iso)
            if d is not None and start <= d <= today and (notes[iso] or "").strip():
                days.append({"date": iso, "text": notes[iso].strip()[:JOURNAL_CHARS]})
        while days and sum(len(x["text"]) for x in days) > ITEM_JOURNAL_CHARS:
            days.pop(0)  # keep the newest days
        journal = "\n\n".join(f"## {x['date']}\n{x['text']}" for x in days)
        out.append({**item, "journal_days": days, "journal": journal})
    return out


MAX_EVIDENCE = 6
MAX_QUOTE = 300


def verify_evidence(raw: Any, journal_days: list[dict[str, str]]) -> dict[str, Any]:
    """Keep only quotes that occur verbatim in a journal day (that day becomes the evidence date)."""
    kept, dropped = [], 0
    seen: set[str] = set()
    for entry in raw if isinstance(raw, list) else []:
        quote = clean(entry.get("quote")) if isinstance(entry, dict) else ""
        if not quote or len(quote) > MAX_QUOTE or normalize(quote) in seen:
            dropped += 1
            continue
        day = next((d["date"] for d in journal_days if normalize(quote) in normalize(d["text"])), None)
        try:
            signal = Signal(str(entry.get("signal", "")).strip().lower())
        except ValueError:
            signal = None
        if day is None or signal is None:
            dropped += 1
            continue
        seen.add(normalize(quote))
        if len(kept) < MAX_EVIDENCE:
            kept.append({"date": day, "quote": quote, "signal": signal.value})
    return {"evidence": kept, "count": len(kept), "dropped": dropped}


_SIGNAL_MARK = {"supports": "+", "contradicts": "-", "neutral": "~"}


def format_evidence(evidence: list[dict[str, str]], limit: int = 160) -> str:
    return " / ".join(
        f'{e["date"]} ({_SIGNAL_MARK.get(e["signal"], "~")}) "{e["quote"][:limit]}"' for e in evidence
    )


def verdict_fields(verdict: Any, evidence: list[dict[str, str]]) -> dict[str, str]:
    """Review fields for one item. Code decides: without verified evidence the verdict is always ``keep``
    (no evidence is no reason to change or drop an item); an unknown verdict value is an error."""
    if not evidence:
        return {
            "evidence": "",
            "verdict": Verdict.KEEP.value,
            "reason": "No evidence in the journals for this period; keep observing.",
            "next_experiment": "",
        }
    v = Verdict(str((verdict or {}).get("verdict", "")).strip().lower())
    return {
        "evidence": format_evidence(evidence),
        "verdict": v.value,
        "reason": clean((verdict or {}).get("reason"))[:400],
        "next_experiment": clean((verdict or {}).get("next_experiment"))[:300],
    }


def proposal_path(folder: str, item_id: str) -> str:
    return f"{folder.strip().rstrip('/')}/{item_id}.md"


def proposal_note(item: dict[str, Any]) -> str:
    """Body of a system-change proposal note: a request for a config change that can only be published
    through the eval gate (spec 013 PublishGate, via the config UI of spec 014)."""
    lines = [
        f"# System change proposal: {item['statement']}",
        "",
        "The owner kept this system improvement. Turning it into a change of prompts, instructions or "
        "config is a **proposal**: it is published only when the eval gate is green (constitution IX).",
        "",
        f"- Item: `{item['id']}` ({item.get('area', 'system')}, {item.get('type', '')})",
        f"- Source: [[{str(item.get('source_note', '')).removesuffix('.md')}]]",
    ]
    if item.get("success_criterion"):
        lines.append(f"- Success criterion: {item['success_criterion']}")
    if item.get("evidence"):
        lines.append(f"- Evidence: {item['evidence']}")
    if item.get("next_experiment"):
        lines.append(f"- Next experiment: {item['next_experiment']}")
    lines += [
        "",
        "## How it is applied",
        "",
        "1. Make the change as a draft in the config UI (or an overlay change set).",
        "2. Publishing runs the affected eval suites; a red gate blocks it.",
        "3. Record the outcome here and set the backlog item's status.",
    ]
    return "\n".join(lines) + "\n"


class ImprovementItem(BaseModel):
    """One backlog row (data-model.md). The last five fields are written by the weekly review only."""

    id: str
    source_note: str
    date: date
    statement: str
    area: Area
    type: ItemType
    success_criterion: str = ""
    review_date: date | None = None
    status: Status = Status.PROPOSED
    evidence: str = ""
    verdict: Verdict | None = None
    reason: str = ""
    next_experiment: str = ""
    proposal: str = ""

    @field_validator("id", "source_note", "statement")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("review_date", "verdict", mode="before")
    @classmethod
    def _blank(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v
