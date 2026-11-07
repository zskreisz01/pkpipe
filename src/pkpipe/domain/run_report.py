"""The run report and the todos it carries (spec 024): pure rules, no I/O.

One report is built after every triage and release run from **counts, category ids, run ids and note links**
only. Item text, URLs from the INBOX and note content never enter it (same rule as the triage message): the
report goes to Discord and, as todo lines, into the daily journal.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

#: Discord allows 2000 characters; the channel truncates beyond its limit, so the report stays below and
#: keeps its todo block whole.
MAX_REPORT_CHARS = 1800
MAX_CATEGORY_NAMES = 8

KIND_TITLE = {"triage": "Triage run", "process_released": "Release run"}

#: label shown for every todo key (the journal line and the Discord block use the same words)
TODO_LABELS = {
    "approval": "Review triage decisions",
    "notebooks": "Process notebook materials and podcasts",
    "tool_reads": "Read tool evaluations",
}
TODO_ORDER = ("approval", "notebooks", "tool_reads")

_SAFE_CHARS = re.compile(r"[^\w \-./()+&,']", re.UNICODE)


@dataclass(frozen=True)
class Todo:
    key: str
    count: int
    link: str | None = None

    @property
    def label(self) -> str:
        return TODO_LABELS[self.key]

    def line(self, day: str) -> str:
        """``- date - todo - related item count - link`` (the owner's format)."""
        noun = "item" if self.count == 1 else "items"
        parts = [day, self.label, f"{self.count} {noun}"]
        if self.link:
            parts.append(self.link)
        return "- " + " - ".join(parts)


def wikilink(path: str | None) -> str | None:
    """``[[note]]`` of a vault path without the extension, or ``None`` when the name is not plain text.

    A note title comes from a model, so it is reduced to plain characters: anything that could break out of
    a link or add markup (``]``, ``|``, ``<``, ``#``, backticks) makes the link ``None``."""
    if not path or not path.endswith(".md"):
        return None
    name = path[: -len(".md")]
    if len(name) > 120 or _SAFE_CHARS.search(name) or ".." in name or name.startswith("/"):
        return None
    return f"[[{name}]]"


def external_link(label: str, url: str | None) -> str | None:
    """``[label](url)`` for the owner's own UI address (config), else ``None``."""
    if not url or not re.fullmatch(r"https?://[^\s()<>\"'`]+", url):
        return None
    return f"[{label}]({url})"


def build_todos(
    approval: int, notebooks: int, tool_reads: int, links: Mapping[str, str | None]
) -> list[Todo]:
    """The todos with a count above zero, in the fixed order of :data:`TODO_ORDER`."""
    counts = {"approval": approval, "notebooks": notebooks, "tool_reads": tool_reads}
    return [Todo(k, counts[k], links.get(k)) for k in TODO_ORDER if counts[k] > 0]


def summarize_results(results: Iterable[Mapping[str, Any]] | None) -> dict[str, Any]:
    """What a release run did, from the results of its item flows (``triage.finalize`` reads the same).

    ``evaluations`` are notes of the category sessions (a note and no notebook), ``notebooks`` the generated
    notebooks that did not fail, ``podcasts`` the notebooks that have an audio file."""
    by_category: Counter[str] = Counter()
    out = {
        "quarantined": 0,
        "failed": 0,
        "evaluations": 0,
        "notebooks": 0,
        "podcasts": 0,
        "notebooks_failed": 0,
    }
    for r in results or []:
        item = r.get("item") or {}
        if r.get("status") != "done":
            out["failed"] += 1
            continue
        if item.get("flagged") or item.get("state") == "quarantined":
            out["quarantined"] += 1
            continue
        by_category[str(item.get("category") or "unknown")] += 1
        failed = item.get("processing_status") == "failed"
        if item.get("nb_id"):
            if failed:
                out["notebooks_failed"] += 1
            else:
                out["notebooks"] += 1
                if item.get("audio_path"):
                    out["podcasts"] += 1
        elif item.get("note") and not failed:
            out["evaluations"] += 1
    return {"by_category": dict(by_category), "processed": sum(by_category.values()), **out}


def summarize_new_rows(rows: Iterable[Mapping[str, Any]] | None) -> dict[str, int]:
    """New triage suggestions per suggested category."""
    return dict(Counter(str(r.get("ai_suggested_category") or "none") for r in rows or []))


def _counts(by_category: Mapping[str, int]) -> str:
    items = sorted(by_category.items(), key=lambda kv: (-kv[1], kv[0]))
    shown = ", ".join(f"{k} {v}" for k, v in items[:MAX_CATEGORY_NAMES])
    if len(items) > MAX_CATEGORY_NAMES:
        shown += f", +{len(items) - MAX_CATEGORY_NAMES} more"
    return shown


def render_report(
    *,
    kind: str,
    day: str,
    clock: str,
    run_id: str,
    summary: Mapping[str, Any],
    awaiting: int,
    todos: list[Todo],
    ui_link: str | None = None,
    notes: list[str] | None = None,
    max_chars: int = MAX_REPORT_CHARS,
) -> str:
    """The Markdown of the report (the first line is the run line; the notification adds the title).

    When it is too long the detail lines shrink first, then the notes; the run line, the waiting line and the
    todo block are kept whole."""
    head = f"{day} {clock} · run {run_id[:8]}"  # the title (KIND_TITLE) is the notification title
    detail: list[str] = []
    if kind == "triage":
        new = summary.get("new_by_category") or {}
        total = sum(new.values())
        detail.append(f"**New suggestions ({total}):** {_counts(new) or 'none'}")
        extra = []
        if summary.get("images_described"):
            extra.append(f"images described {summary['images_described']}")
        if summary.get("images_deferred"):
            extra.append(f"images deferred {summary['images_deferred']} (first in line next run)")
        if summary.get("quarantined"):
            extra.append(f"quarantined {summary['quarantined']}")
        if extra:
            detail.append(" · ".join(extra))
    else:
        by_cat = summary.get("by_category") or {}
        line = f"**Processed ({summary.get('processed', 0)}):** {_counts(by_cat) or 'none'}"
        if summary.get("quarantined") or summary.get("failed"):
            line += f" · quarantined {summary.get('quarantined', 0)} · failed {summary.get('failed', 0)}"
        detail.append(line)
        created = [
            f"{summary[k]} {name}"
            for k, name in (
                ("evaluations", "evaluations"),
                ("notebooks", "notebooks"),
                ("podcasts", "podcasts"),
            )
            if summary.get(k)
        ]
        detail.append("**Created:** " + (" · ".join(created) if created else "nothing"))
        if summary.get("notebooks_failed"):
            detail.append(f"Notebooks that failed: {summary['notebooks_failed']} (retried by the next run)")
        if summary.get("proposals"):
            detail.append(f"New-category proposals written: {summary['proposals']}")
    waiting = f"**Waiting for you:** {awaiting} decision(s) in the triage" + (
        f" ({ui_link})" if ui_link else ""
    )
    todo_block = ["**Todo**", *[t.line(day) for t in todos]] if todos else ["**Todo:** nothing open"]
    notes = [n for n in (notes or []) if n]
    text = "\n".join([head, *detail, waiting, *notes, *todo_block])
    if len(text) > max_chars:  # header, awaiting line and todo block stay whole; detail, then notes, shrink
        room = max_chars - len("\n".join([head, waiting, *todo_block])) - 2
        detail_text = "\n".join(detail)[: max(room, 0)].rstrip()
        room -= len(detail_text) + 1
        notes_text = "\n".join(notes)[: max(room, 0)].rstrip()
        text = "\n".join(p for p in (head, detail_text, waiting, notes_text, *todo_block) if p)
    return text
