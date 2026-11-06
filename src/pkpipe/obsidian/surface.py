"""Where the triage table lives (spec 023): the ``vault`` and the ``web`` surface, and the move between them.

* ``vault``: the table is in ``layout.triage_file`` (the file the owner edits in Obsidian), as before.
* ``web``: the machine-owned table is in ``layout.triage_data_file``; ``layout.triage_file`` is a generated
  page (front matter, title, the link to the web UI, a short summary) **without a table**, so there is
  nothing to edit in two places.

The functions here are pure text functions. The table block (header, separator, every row, also rows that
do not parse) moves as text, so every cell survives a switch (stray ``[ x]``, image embeds
``![[x.jpg\\|200]]``, any number of columns). :class:`TriageSurface` does the file work under the usual
locks: the target first, then the source, with a re-plan when the source changed in between.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pkpipe.domain.triage import ItemState, is_unreviewed

from . import lists
from .atomic import update_text
from .errors import WriteConflict
from .lists import TRIAGE_PREAMBLE
from .notes import parse_note
from .tables import _SEP_CELL, split_cells

log = logging.getLogger("pkpipe.vault")

PAGE_MARKER = "<!-- pkpipe:triage-summary -->"
DEFAULT_PAGE_FRONT = "---\npkpipe: triage-summary\n---\n"
DATA_PREAMBLE = (
    "# Triage data\n\n"
    "Machine-owned table of the pkpipe triage (`triage.surface: web`). Decide in the web UI: this file is "
    "written by pkpipe and the UI, do not edit it by hand.\n\n"
)
MOVED_HEADING = "## Moved from the triage page"
_LAST_RUN = re.compile(r"^Last triage run: (.+)$", re.MULTILINE)
MAX_REPLANS = 3


# -- the table block --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class TableBlock:
    """The first triage table of a text: ``before`` (the lines above), the table lines, ``after``."""

    before: str
    table: str
    after: str
    header: tuple[str, ...]

    @property
    def rows(self) -> list[str]:
        """The table lines below the header and the separator."""
        return self.table.splitlines(keepends=True)[2:]


def split_table(text: str) -> TableBlock | None:
    """The first triage table of ``text`` (a header with the ``item`` and ``resource`` columns followed by a
    separator row and the lines that start with ``|``), or None."""
    lines = text.splitlines(keepends=True)
    for idx, raw in enumerate(lines[:-1]):
        body = raw.strip()
        if not body.startswith("|"):
            continue
        names = tuple(c.lower() for c in split_cells(body))
        if "item" not in names or "resource" not in names:
            continue
        sep = split_cells(lines[idx + 1].strip()) if lines[idx + 1].strip().startswith("|") else []
        if not sep or not all(_SEP_CELL.match(c) for c in sep):
            continue
        end = idx + 2
        while end < len(lines) and lines[end].strip().startswith("|"):
            end += 1
        return TableBlock("".join(lines[:idx]), "".join(lines[idx:end]), "".join(lines[end:]), names)
    return None


def has_table(text: str | None) -> bool:
    return bool(text) and split_table(text or "") is not None


def _row_ids(block: TableBlock) -> set[str]:
    at = block.header.index("item")
    ids = set()
    for line in block.rows:
        cells = split_cells(line.strip())
        if len(cells) > at and cells[at]:
            ids.add(cells[at])
    return ids


def _with_eol(text: str, eol: str) -> str:
    text = text.replace("\r\n", "\n")
    return text.replace("\n", eol) if eol != "\n" else text


def merge_into(target: str, source: TableBlock) -> str:
    """``target`` (which holds a table already: an interrupted move) plus the rows of ``source`` it lacks,
    verbatim, after its last row. The rows of the target stay as they are."""
    have = split_table(target)
    if have is None:
        return target
    ids = _row_ids(have)
    at = source.header.index("item")
    known_lines = {ln.strip() for ln in have.rows}
    extra = []
    for line in source.rows:
        cells = split_cells(line.strip())
        item = cells[at] if len(cells) > at else ""
        if (item and item in ids) or line.strip() in known_lines:
            continue
        extra.append(line if line.endswith(("\n", "\r")) else line + "\n")
    if not extra:
        return target
    table = have.table if have.table.endswith(("\n", "\r")) else have.table + "\n"
    return have.before + table + "".join(extra) + have.after


# -- prose and front matter -------------------------------------------------------------------------------
def front_matter_of(text: str) -> str:
    """The front matter block of a page (fences included), or ''."""
    return parse_note(text).raw_header


def extra_prose(text: str) -> str:
    """What a retired page holds besides front matter, table and the generated words: owner prose that must
    not be lost when the page is rewritten."""
    note = parse_note(text)
    block = split_table(note.body)
    body = (block.before + block.after) if block else note.body
    keep = []
    generic = {
        ln.strip() for ln in (*TRIAGE_PREAMBLE.splitlines(), *DATA_PREAMBLE.splitlines(), MOVED_HEADING)
    }
    skipping = False
    for raw in body.splitlines():
        line = raw.strip()
        if line == PAGE_MARKER or line == "<!-- /pkpipe:triage-summary -->":
            skipping = line == PAGE_MARKER
            continue
        if skipping:
            continue
        if not line or line in generic or re.match(r"^# .*triage.*$", line, re.IGNORECASE):
            if not line and keep and keep[-1] != "":
                keep.append("")
            continue
        keep.append(raw.rstrip())
    return "\n".join(keep).strip()


def _last_run(text: str) -> str | None:
    match = _LAST_RUN.search(text)
    return match.group(1).strip() if match else None


# -- the summary page of the web surface ----------------------------------------------------------------
def summarise(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts for the page: per state, rows that wait for a decision, rows that need the owner (images), and
    new-category proposals."""
    states: Counter[str] = Counter()
    waiting = needs_you = 0
    for r in rows:
        state = str(r.get("state") or "suggested")
        states[state] += 1
        try:
            unreviewed = is_unreviewed(ItemState(state), bool(r.get("confirm")), r.get("override") or None)
        except ValueError:
            unreviewed = False
        if unreviewed:
            waiting += 1
            needs_you += bool(r.get("needs_owner"))
    return {
        "total": len(rows),
        "states": dict(sorted(states.items())),
        "waiting": waiting,
        "needs_owner": needs_you,
        "proposals": states.get(ItemState.AWAITING_CATEGORY.value, 0),
    }


def render_page(
    *, front: str, url: str | None, data_file: str, summary: Mapping[str, Any], last_run: str | None
) -> str:
    """The generated triage page of the ``web`` surface. No table: the data is in ``data_file``."""
    link = (
        f"Decide in the web UI: [{url}/triage]({url}/triage)"
        if url
        else "Decide in the web UI (set `ui.public_url` in the config to show its address here)."
    )
    states = summary["states"]
    counts = " · ".join(f"{name} {n}" for name, n in states.items()) or "no items yet"
    lines = [
        front.rstrip("\n") if front else DEFAULT_PAGE_FRONT.rstrip("\n"),
        "",
        "# Inbox triage",
        "",
        link,
        "",
        PAGE_MARKER,
        f"- Waiting for your decision: {summary['waiting']}",
        f"- Images that need a category from you: {summary['needs_owner']}",
        f"- New-category proposals awaiting a category: {summary['proposals']}",
        f"- All rows: {summary['total']} ({counts})",
        f"Last triage run: {last_run or 'not yet'}",
        "<!-- /pkpipe:triage-summary -->",
        "",
        "This page is generated by pkpipe on every triage run and every decision in the UI; changes made "
        "here are "
        f"overwritten. The triage table is kept in `{data_file}` (machine-owned).",
        "",
    ]
    return "\n".join(lines)


def tombstone(page: str) -> str:
    """The data file after the table moved back to the triage file (``vault`` surface): no table."""
    name = Path(page).stem
    return (
        "# Triage data (not in use)\n\n"
        f"`triage.surface` is `vault`: the triage table is in [[{name}]]. pkpipe no longer writes this file; "
        "it is safe to delete it.\n"
    )


# -- the file work ----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class MigrationPlan:
    direction: str  # "to_web" | "to_vault"
    source: str
    target: str
    rows: int
    invalid: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "source": self.source,
            "target": self.target,
            "rows": self.rows,
            "invalid": self.invalid,
        }


class _Replan(Exception):
    """The source changed between planning and the locked rewrite."""


class TriageSurface:
    """The two files of the triage table and the surface that decides which one holds it.

    ``page`` / ``data`` are the resolved absolute paths of ``layout.triage_file`` and
    ``layout.triage_data_file``; ``page_rel`` / ``data_rel`` their vault relative names (for the page text).
    """

    def __init__(
        self,
        *,
        surface: str,
        page: Path,
        data: Path,
        page_rel: str,
        data_rel: str,
        public_url: str | None = None,
        now: Callable[[], datetime] = datetime.now,
    ):
        self.surface, self.page, self.data = surface, page, data
        self.page_rel, self.data_rel = page_rel, data_rel
        self.public_url, self._now = public_url, now

    # -- where the table is ------------------------------------------------------------------------------
    @staticmethod
    def _table_in(path: Path) -> bool:
        try:
            return has_table(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False

    @property
    def preferred(self) -> Path:
        return self.data if self.surface == "web" else self.page

    @property
    def other(self) -> Path:
        return self.page if self.surface == "web" else self.data

    def holder(self) -> Path:
        """The file that holds the table now: the surface's own file, or, while a switch has not been moved
        yet, the other one (reads and owner decisions never see an empty table)."""
        if self._table_in(self.preferred) or not self._table_in(self.other):
            return self.preferred
        return self.other

    def plan(self) -> MigrationPlan | None:
        """What the next triage write moves, or None when nothing is to move."""
        try:
            text = self.other.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        block = split_table(text)
        if block is None:
            return None
        invalid = sum(1 for ln in block.rows if not self._parses(ln, block.header))
        return MigrationPlan(
            "to_web" if self.surface == "web" else "to_vault",
            self.other_rel,
            self.preferred_rel,
            len(block.rows) - invalid,
            invalid,
        )

    @property
    def preferred_rel(self) -> str:
        return self.data_rel if self.surface == "web" else self.page_rel

    @property
    def other_rel(self) -> str:
        return self.page_rel if self.surface == "web" else self.data_rel

    @staticmethod
    def _parses(line: str, header: tuple[str, ...]) -> bool:
        cells = split_cells(line.strip())
        return len(cells) == len(header) and bool(cells[header.index("item")])

    # -- the move ----------------------------------------------------------------------------------------
    def migrate(self) -> MigrationPlan | None:
        """Move the table to the surface's file, verbatim and idempotent. Logs the plan first."""
        plan = self.plan()
        if plan is None:
            return None
        log.info(
            "triage migration plan: %s, %d row(s) (%d unreadable kept as they are), %s -> %s",
            plan.direction,
            plan.rows,
            plan.invalid,
            plan.source,
            plan.target,
        )
        for _ in range(MAX_REPLANS):
            source_text = self.other.read_text(encoding="utf-8")
            block = split_table(source_text)
            if block is None:
                return plan
            update_text(self.preferred, self._target_text(block, source_text))

            def retire(text: str | None, expected: str = block.table) -> str:
                now = split_table(text or "")
                if now is None:
                    return text or ""  # already moved by another run
                if now.table != expected:
                    raise _Replan  # the source changed after the copy: copy again
                return self._retired(text or "")

            try:
                update_text(self.other, retire)
            except _Replan:
                continue
            log.info("triage migration done: %s", plan.direction)
            return plan
        raise WriteConflict(f"{self.other.name} kept changing while the triage table moved; retry")

    def _target_text(self, block: TableBlock, source_text: str) -> Callable[[str | None], str]:
        moved = extra_prose(source_text)

        def build(existing: str | None) -> str:
            if existing is not None and has_table(existing):
                return merge_into(existing, block)
            eol = "\r\n" if "\r\n" in source_text else "\n"
            table = _with_eol(block.table if block.table.endswith("\n") else block.table + "\n", eol)
            if self.surface == "web":
                head = DATA_PREAMBLE
                tail = f"\n{MOVED_HEADING}\n\n{moved}\n" if moved else ""
            else:
                front = front_matter_of(existing or "") or front_matter_of(source_text)
                keep = front if front and DEFAULT_PAGE_FRONT.strip() not in front else ""
                head = keep + TRIAGE_PREAMBLE
                tail = f"\n{moved}\n" if moved else ""
            return _with_eol(head, eol) + table + _with_eol(tail, eol)

        return build

    def _retired(self, text: str) -> str:
        """The text the source file gets once its table moved."""
        if self.surface == "vault":
            return tombstone(self.page_rel)
        rows = self._rows_of(self.data)
        return render_page(
            front=front_matter_of(text),
            url=self.public_url,
            data_file=self.data_rel,
            summary=summarise(rows),
            last_run=_last_run(text),
        )

    @staticmethod
    def _rows_of(path: Path) -> list[dict[str, Any]]:
        try:
            return lists.read_triage(path)["rows"]
        except FileNotFoundError:
            return []

    # -- the generated page ------------------------------------------------------------------------------
    def refresh_page(self, *, stamp: bool) -> bool:
        """Rewrite the generated page (``web`` surface, table in the data file): counts, the run
        time when ``stamp`` (a triage run) else the previous one. Returns whether the file changed."""
        if self.surface != "web" or self.holder() != self.data:
            return False
        summary = summarise(self._rows_of(self.data))
        stamp_text = self._now().strftime("%Y-%m-%d %H:%M")
        changed = False

        def build(existing: str | None) -> str:
            nonlocal changed
            old = existing or ""
            if has_table(old):
                return old  # a table is never overwritten: the move did not run yet
            if old.strip() and PAGE_MARKER not in old:
                prose = extra_prose(old)
                if prose:  # a page that was not generated: its prose is kept in the data file
                    self._keep_prose(prose)
            text = render_page(
                front=front_matter_of(old),
                url=self.public_url,
                data_file=self.data_rel,
                summary=summary,
                last_run=stamp_text if stamp else _last_run(old),
            )
            changed = text != old
            return text

        update_text(self.page, build)
        return changed

    def _keep_prose(self, prose: str) -> None:
        def add(text: str | None) -> str:
            base = text or DATA_PREAMBLE
            if prose in base:
                return base
            return base.rstrip("\n") + f"\n\n{MOVED_HEADING}\n\n{prose}\n"

        update_text(self.data, add)
