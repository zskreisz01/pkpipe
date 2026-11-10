"""The unfinished items of the INBOX (spec 031). Pure logic: which items are unfinished, what the table
``INBOX-InProgress.md`` looks like, how a row's facts are hashed and what the fixed-sentence fallback says.

An item is *unfinished* when it was worked on (triage state ``processed`` or ``quarantined``) and the work is
not closed: its processing-list status is ``failed`` or ``unprocessed``, it is held for the owner's decision,
or it is quarantined. ``done`` and ``ready`` close an item. Items still awaiting a category, pending or
suggested are not here: nothing was processed yet.

Code decides all of this (constitution V). The model only writes the three short sentences of a row from the
facts; it never sees page, note or image content.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

COLUMNS = (
    "item",
    "kind",
    "resource",
    "category",
    "state",
    "situation",
    "cause",
    "next step",
    "since",
    "last run",
    "history",
    "entry",
    "facts",
)
CLOSED_STATUSES = frozenset({"done", "ready"})
OPEN_STATUSES = frozenset({"failed", "unprocessed"})
MAX_SITUATION, MAX_CAUSE, MAX_NEXT = 240, 80, 200
MAX_RESOURCE = 200
MAX_ENTRY = 400
HEADER = (
    "# INBOX in progress\n\n"
    "Machine-owned table of the items pkpipe worked on but has not closed (processing failed, held for your "
    "decision, or quarantined). pkpipe writes it after every release run and the UI/CLI show the same facts; "
    "do not edit it by hand. A closed item (done, ready) leaves the table; its entry is archived like a "
    "consumed one.\n\n"
)

# cause codes, from the fixed failure texts of the processing list
_CAUSES = (
    ("held for your decision", "held_classifier"),
    ("no source found by web search", "no_source_found"),
    ("no source found: nothing searchable", "no_source_found"),
    ("no source found: the image has no cached description", "no_description"),
    ("no source found: the search query was refused", "query_blocked"),
    ("no source found: the web search failed", "search_failed"),
    ("no source found", "no_source_found"),
    ("no source url", "no_source_url"),
    ("page could not be fetched", "page_not_fetched"),
    ("modelunavailable", "model_unavailable"),
    ("model has crashed", "model_unavailable"),
    ("failed", "session_failed"),
)
NEXT_STEPS = {
    "held_classifier": "Open the item in the triage page, read the scores, accept the warning or leave it.",
    "quarantined": "Look at the item history; release it from the quarantine if it is a false positive.",
    "no_source_found": "Add a link to the entry and release it again, or retry once the search works.",
    "no_description": "Triage the image again so it gets a description.",
    "query_blocked": "Add a link to the entry; the web query guard refused the search query.",
    "search_failed": "Retry: the next release run searches again.",
    "no_source_url": "Add the source link to the entry and release it again.",
    "page_not_fetched": "Check that the page is reachable, then retry.",
    "model_unavailable": "Check that the local model is running, then retry.",
    "session_failed": "Look at the item history for the failing step, then retry.",
    "retry_limit": "The retry limit is reached: fix the cause or process it by hand.",
    "unfinished": "Look at the item history, then retry or process it by hand.",
}


def cause_code(state: str, status: str, reason: str, *, held: bool) -> str:
    """A stable code for why the item is not closed (from the state, the status and the fixed reason text)."""
    if held:
        return "held_classifier"
    if state == "quarantined":
        return "quarantined"
    text = (reason or "").lower()
    for needle, code in _CAUSES:
        if needle in text:
            return code
    return "unfinished" if status in OPEN_STATUSES or not status else "unfinished"


def is_unfinished(state: str, status: str, *, held: bool = False) -> bool:
    """The item was worked on and is not closed (see the module text)."""
    if state == "quarantined":
        return True
    if state in ("processed", "released"):
        if held:
            return True
        return status in OPEN_STATUSES
    return False


def is_closed(state: str, status: str) -> bool:
    return state == "processed" and status in CLOSED_STATUSES


def outcome(state: str, status: str, *, held: bool = False, failed: bool = False) -> tuple[str, str]:
    """``(code, label)`` the triage page shows instead of the bare state ``processed`` (spec 031).

    ``processed`` only means the entry was worked on. The outcome says how it went; every other state is
    shown as it is."""
    if state != "processed":
        return state, state
    if held:
        return "held", "held for you"
    if status in CLOSED_STATUSES:
        return status, status
    if failed or status == "failed":
        return "failed", "failed"
    if status == "unprocessed":
        return "unprocessed", "not finished"
    return "processed", "processed (result unknown)"


def cell(value: Any, limit: int) -> str:
    """One table cell: single line, no pipes, no Markdown that would reshape the table or run as markup."""
    text = " ".join(str(value or "").split())[:limit]
    text = text.replace("|", "\\|").replace("`", "'").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\[\]*_~])", r"\\\1", text) if text else ""


def uncell(value: str) -> str:
    return re.sub(r"\\([\\|\[\]*_~])", r"\1", value.replace("&lt;", "<").replace("&gt;", ">")).strip()


def encode_entry(raw: str) -> str:
    """The raw INBOX entry in one table cell, exactly restorable: a code span (so an image embed does not
    render and no markup runs) with backslash, newline, pipe and backtick escaped."""
    enc = raw.replace("\\", "\\\\").replace("\n", "\\n").replace("|", "\\p").replace("`", "\\b")
    enc = enc.replace("\x1e", "\\r")
    return f"`{enc[: MAX_ENTRY * 3]}`" if enc else ""


def decode_entry(cellvalue: str) -> str:
    body = cellvalue.strip()
    if body.startswith("`") and body.endswith("`") and len(body) >= 2:
        body = body[1:-1]
    out, i = [], 0
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body):
            out.append({"n": "\n", "p": "|", "b": "`", "r": "\x1e", "\\": "\\"}.get(body[i + 1], body[i + 1]))
            i += 2
        else:
            out.append(body[i])
            i += 1
    return "".join(out)


def facts_hash(facts: dict[str, Any]) -> str:
    """A short hash of what is known now; a row whose hash did not change is not asked again."""
    raw = json.dumps(facts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def facts_text(facts: dict[str, Any]) -> str:
    """The facts as the lines the model reads. Only our own records: ids, codes, numbers, fixed sentences."""
    lines = []
    for key, value in facts.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines += [f"  - {str(v)[:220]}" for v in value]
        elif isinstance(value, dict):
            lines.append(f"{key}: " + json.dumps(value, ensure_ascii=False, default=str)[:600])
        else:
            lines.append(f"{key}: {str(value)[:300]}")
    return "\n".join(lines)


def fallback_row(facts: dict[str, Any]) -> dict[str, str]:
    """The fixed-sentence situation when the model is down or its answer is unusable."""
    code = str(facts.get("cause") or "unfinished")
    state, status = str(facts.get("triage_state") or ""), str(facts.get("processing_status") or "")
    reason = str(facts.get("failure_reason") or "")
    situation = f"{state or 'item'}: {status or 'no status'}" + (f"; {reason}" if reason else "")
    retries = facts.get("owner_retries")
    if retries:
        situation += f" (retried {retries}x)"
    return {
        "situation": situation[:MAX_SITUATION],
        "cause": code,
        "next_step": NEXT_STEPS.get(code, NEXT_STEPS["unfinished"])[:MAX_NEXT],
    }


def clean_answer(answer: Any, facts: dict[str, Any]) -> dict[str, str]:
    """The model's answer cut to its limits; anything missing or not text falls back to the fixed sentence."""
    base = fallback_row(facts)
    if not isinstance(answer, dict):
        return base
    out = dict(base)
    for key, limit in (("situation", MAX_SITUATION), ("cause", MAX_CAUSE), ("next_step", MAX_NEXT)):
        value = answer.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = " ".join(value.split())[:limit]
    return out


def render_row(row: dict[str, Any], *, ui_url: str | None = None) -> str:
    """One Markdown table line of the row (every cell escaped)."""
    item = str(row["item_id"])
    history = f"[history]({ui_url.rstrip('/')}/items/{item})" if ui_url else ""
    cells = [
        item,
        cell(row.get("kind"), 20),
        cell(row.get("resource"), MAX_RESOURCE),
        cell(row.get("category"), 40),
        cell(row.get("state"), 40),
        cell(row.get("situation"), MAX_SITUATION),
        cell(row.get("cause"), MAX_CAUSE),
        cell(row.get("next_step"), MAX_NEXT),
        cell(row.get("since"), 20),
        cell(row.get("last_run"), 20),
        history,
        encode_entry(str(row.get("entry") or "")),
        cell(row.get("facts"), 20),
    ]
    return "| " + " | ".join(cells) + " |"


def render_table(rows: list[dict[str, Any]], *, ui_url: str | None = None) -> str:
    head = "| " + " | ".join(COLUMNS) + " |\n| " + " | ".join("---" for _ in COLUMNS) + " |\n"
    return HEADER + head + "".join(render_row(r, ui_url=ui_url) + "\n" for r in rows)


def _split(line: str) -> list[str]:
    cells, cur, i = [], "", 0
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            cur += body[i : i + 2]
            i += 2
            continue
        if ch == "|":
            cells.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    cells.append(cur)
    return [c.strip() for c in cells]


def parse_table(text: str | None) -> list[dict[str, str]]:
    """The rows of a table this module wrote (keys are the column names, cells unescaped). A line that is not
    a row of that table (the header, a hand edit) is skipped, never an error."""
    rows: list[dict[str, str]] = []
    for line in (text or "").splitlines():
        if not line.startswith("|"):
            continue
        cells = _split(line)
        if len(cells) != len(COLUMNS) or not re.fullmatch(r"[0-9a-f]{6,64}", cells[0]):
            continue
        row = {
            col: uncell(c) if col not in ("history", "entry") else c
            for col, c in zip(COLUMNS, cells, strict=True)
        }
        row["entry"] = decode_entry(row["entry"])
        row["item_id"] = row.pop("item")
        row["next_step"] = row.pop("next step")
        row["last_run"] = row.pop("last run")
        rows.append(row)
    return rows
