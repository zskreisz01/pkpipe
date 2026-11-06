"""Triage file and processing list as structured rows (spec 004 US2, FR-001, T004-T006)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pkpipe.domain.triage import (
    ItemState,
    OwnerDecisionError,
    check_owner_decision,
    check_quarantine_release,
    check_transition,
)

from .atomic import update_text
from .errors import InvalidRows
from .models import EMBED_CELL, ProcessingListRow, TriageRow, item_id_for
from .policy import is_conflict_name
from .tables import TableFile

TRIAGE_COLUMNS = ("confirm", "item", "resource", "suggested", "override", "state")
#: Added by spec 020 (``kind``, ``needs_owner``), spec 021 (``topic``) and spec 022 (``comment``, the
#: owner's own words). Every write gives the table the full header (:func:`_prepare_table`): a file written
#: before them is widened on its next write, no row or cell lost.
TRIAGE_OPTIONAL = ("kind", "needs_owner", "topic", "comment")
PROCESSING_COLUMNS = ("item", "category", "note", "nb_id", "link", "status")
TRIAGE_PREAMBLE = (
    "# Inbox triage\n\n"
    "Tick **confirm** if the suggested category is right, or write a category id into **override**.\n"
    "Rows you leave alone stay pending.\n\n"
)
PROCESSING_PREAMBLE = "# Manual resource processing list\n\n"


def find_conflicts(path: Path) -> list[str]:
    """Sync conflict copies next to ``path`` (``<stem> (conflict).md``); reported, never read or merged."""
    if not path.parent.exists():
        return []
    return sorted(
        p.name
        for p in path.parent.iterdir()
        if p.name.startswith(path.stem) and p.name != path.name and is_conflict_name(p.name)
    )


def _load(path: Path, columns: tuple[str, ...], preamble: str, optional: tuple[str, ...] = ()) -> TableFile:
    text = path.read_text(encoding="utf-8") if path.exists() else None
    return TableFile(text, columns, key="item", preamble=preamble, optional=optional)


def read_triage(path: Path) -> dict[str, Any]:
    table = _load(path, TRIAGE_COLUMNS, TRIAGE_PREAMBLE, TRIAGE_OPTIONAL)
    rows: list[TriageRow] = []
    invalid = [{"line": n, "text": t} for n, t in table.invalid]
    for cells in table.rows():
        try:
            rows.append(TriageRow.from_cells(cells))
        except (ValueError, ValidationError):
            invalid.append({"line": 0, "text": f"item {cells['item']}: unreadable row"})
    return {
        "rows": [r.model_dump(mode="json") for r in rows],
        "invalid": invalid,
        "conflicts": find_conflicts(path),
    }


OWNER_FIELDS = ("confirm", "override")
#: Fixed when the row is created (spec 020): ``needs_owner`` is the human-in-the-loop marker of an image.
CREATION_FIELDS = ("kind", "needs_owner")


def _normalise_triage_update(update: dict[str, Any], existing: dict[str, str] | None) -> TriageRow:
    """Merge ``update`` into the file's row and enforce the rules the pipeline may not bypass.

    Constitution VI: ``confirm`` and ``override`` are the owner's decisions, made in Obsidian; the pipeline
    may repeat their current value but never change it, and new rows start ``suggested`` (or
    ``quarantined`` when the output guard flagged the item at triage, spec 009).
    Constitution V: state changes follow :data:`pkpipe.domain.triage.TRANSITIONS`, so ``released`` needs a
    confirm tick or an override.
    """
    data = {k: v for k, v in update.items() if k in TriageRow.model_fields}
    if existing is None:
        if data.get("confirm") or (isinstance(data.get("override"), str) and data["override"].strip()):
            raise ValueError("confirm and override are set by the owner, not by the pipeline")
        new_states = (ItemState.SUGGESTED.value, ItemState.QUARANTINED.value)
        if "state" in data and str(data["state"]).strip().lower() not in new_states:
            raise ValueError("new triage rows start in state 'suggested' (or 'quarantined' when flagged)")
        if data.get("comment"):
            raise ValueError("comment is the owner's: the pipeline never writes it")
        if not data.get("resource"):
            raise ValueError("resource is required for a new row")
        expected = item_id_for(_plain_resource(data["resource"]))
        if "item_id" in data and str(data["item_id"]).strip() != expected:
            raise ValueError("item_id of a new row must be the hash of its resource")
        data["item_id"] = expected
        row = TriageRow(**data)
        if row.needs_owner and row.kind != "image":
            raise ValueError("needs_owner is set for image items only")
        return row
    current = TriageRow.from_cells(existing)
    candidate = TriageRow(**{**current.model_dump(), **data})
    for field in OWNER_FIELDS:
        if field in data and getattr(candidate, field) != getattr(current, field):
            raise ValueError(f"{field} is set by the owner, not by the pipeline")
    if "comment" in data and data["comment"] != current.comment:
        raise ValueError("comment is the owner's: the pipeline never writes it")
    for field in CREATION_FIELDS:
        if field in data and getattr(candidate, field) != getattr(current, field):
            raise ValueError(f"{field} is fixed when the row is created")
    if "resource" in data and candidate.resource != current.resource:
        raise ValueError("the resource of an existing row cannot change (the item id is its hash)")
    reason = check_transition(
        current.state, candidate.state, owner_decided=current.confirm or current.override is not None
    )
    if reason:
        raise ValueError(reason)
    return candidate


def update_triage(
    path: Path, updates: list[dict[str, Any]], *, preamble: str = TRIAGE_PREAMBLE
) -> dict[str, Any]:
    """Upsert rows by item id.

    Provided fields overwrite, omitted fields keep the file's current value. ``confirm`` and ``override``
    can only be changed by the owner in the file, new rows start ``suggested`` with ``item_id`` = hash of the
    resource, and state changes follow the domain transition rules. The whole batch is validated before
    anything is written.
    """
    result: dict[str, list[str]] = {}
    if (
        not updates
    ):  # the maintenance call (spec 022): the column migration only, nothing is written if current
        return _maintain_triage(path, preamble)

    def transform(text: str | None) -> str:
        table = TableFile(text, TRIAGE_COLUMNS, key="item", preamble=preamble, optional=TRIAGE_OPTIONAL)
        _prepare_table(table)
        outcome: dict[str, list[str]] = {"inserted": [], "updated": [], "unchanged": []}
        errors: list[str] = []
        for i, update in enumerate(updates):
            if "item_id" not in update and update.get("resource"):
                update = {**update, "item_id": item_id_for(_plain_resource(update["resource"]))}
            key = str(update.get("item_id", "")).strip()
            existing = table.get(key) if key else None
            try:
                row = _normalise_triage_update(update, existing)
            except (ValueError, ValidationError) as exc:
                errors.append(f"row {i}: {_short(exc)}")
                continue
            outcome[table.upsert(row.to_cells())].append(row.item_id)
        if errors:
            raise InvalidRows("; ".join(errors))
        width = max((int(u.get("embed_width") or 0) for u in updates), default=0)
        if width:  # a run that shows images: image rows written before the embed existed get it too
            for cells in table.rows():
                if cells.get("kind") != "image":
                    continue
                try:
                    old = TriageRow.from_cells(cells)
                except (ValueError, ValidationError):
                    continue
                if old.embed_width is None and not old.resource_cell().startswith("!["):
                    row = old.model_copy(update={"embed_width": width})
                    if table.upsert(row.to_cells()) == "updated":
                        outcome["updated"].append(row.item_id)
        result.update(outcome)
        return table.render()

    update_text(path, transform)
    return {**result, "conflicts": find_conflicts(path)}


def _prepare_table(table: TableFile) -> None:
    """Every write: the column migration of spec 022, then the full header.

    A table whose header has no ``comment`` column was written before the owner had one. Its non-image rows
    can only hold text in ``topic`` that the owner typed (the pipeline writes a topic for image rows only), so
    that text moves into ``comment``, once: the rule runs only while the header lacks the column, and the
    header is full afterwards. The topic of an image row is the pipeline's and stays."""
    if table.has_header and not table.header_has("comment"):
        for cells in table.rows():
            if cells.get("kind") != "image" and cells.get("topic"):
                table.upsert({**cells, "comment": cells.get("comment") or cells["topic"], "topic": ""})
    table.widen()


def _maintain_triage(path: Path, preamble: str = TRIAGE_PREAMBLE) -> dict[str, Any]:
    """``update_triage([])``: migrate and widen the table of an existing file; write only when it changes."""
    empty: dict[str, list[str]] = {"inserted": [], "updated": [], "unchanged": []}
    if not path.exists():
        return {**empty, "conflicts": find_conflicts(path)}

    def transform(text: str | None) -> str:
        table = TableFile(text, TRIAGE_COLUMNS, key="item", preamble=preamble, optional=TRIAGE_OPTIONAL)
        _prepare_table(table)
        return table.render()

    current = path.read_text(encoding="utf-8")
    if transform(current) != current:
        update_text(path, transform)
    return {**empty, "conflicts": find_conflicts(path)}


def _plain_resource(value: object) -> str:
    """The name inside an embed (``![[name|200]]``), or the resource as given."""
    text = str(value).strip()
    match = EMBED_CELL.match(text)
    return match.group(1).strip() if match else text


def _owner_edit(path: Path, item_id: str, change, preamble: str = TRIAGE_PREAMBLE) -> dict[str, Any]:
    """Apply ``change(current_row) -> new_row`` to one existing triage row under the file lock."""
    result: dict[str, Any] = {}

    def transform(text: str | None) -> str:
        table = TableFile(text, TRIAGE_COLUMNS, key="item", preamble=preamble, optional=TRIAGE_OPTIONAL)
        _prepare_table(table)
        existing = table.get(item_id)
        if existing is None:
            raise InvalidRows(f"unknown triage item '{item_id}'")
        try:
            current = TriageRow.from_cells(existing)
        except (ValueError, ValidationError):
            raise InvalidRows(f"item '{item_id}' has an unreadable row") from None
        try:
            new = change(current)
        except OwnerDecisionError as exc:
            raise InvalidRows(str(exc)) from None
        table.upsert(new.to_cells())
        result.update(new.model_dump(mode="json"))
        return table.render()

    update_text(path, transform)
    return result


def owner_decide(
    path: Path,
    item_id: str,
    *,
    confirm: bool | None = None,
    override: str | None = None,
    categories: Mapping[str, Mapping[str, object]] | set[str] | frozenset[str] | None = None,
    preamble: str = TRIAGE_PREAMBLE,
) -> dict[str, Any]:
    """The owner's confirm tick and/or override category for one item, written to the triage file.

    This is the only writer of ``confirm`` / ``override`` besides the owner's own edit in Obsidian. It is
    reachable from the owner channels (UI, authenticated app MCP) and never from the vault MCP server, which
    sessions may use. Decision rules: :func:`pkpipe.domain.triage.check_owner_decision`.
    """

    def change(row: TriageRow) -> TriageRow:
        value = check_owner_decision(row.state, confirm=confirm, override=override, categories=categories)
        data = row.model_dump()
        if confirm is not None:
            data["confirm"] = confirm
        if override is not None:
            data["override"] = value or None
        return TriageRow(**data)

    return _owner_edit(path, item_id, change, preamble)


COMMENT_MAX_CHARS = 1000
_COMMENT_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_comment(value: object) -> str:
    """The comment the owner typed, fit for one table cell: control characters dropped, line breaks and tabs
    become blanks, at most :data:`COMMENT_MAX_CHARS` characters. Nothing else is changed (no neutralising:
    it is the owner's own text, and a model gets it only as a datamarked, capped input)."""
    text = str(value or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return _COMMENT_CONTROL.sub("", text).strip()[:COMMENT_MAX_CHARS]


def owner_comment(path: Path, item_id: str, comment: str, preamble: str = TRIAGE_PREAMBLE) -> dict[str, Any]:
    """The owner's comment of one row (owner channels only: the UI; the vault tools refuse a comment)."""

    def change(row: TriageRow) -> TriageRow:
        return row.model_copy(update={"comment": clean_comment(comment)})

    return _owner_edit(path, item_id, change, preamble)


def release_quarantine(path: Path, item_id: str, preamble: str = TRIAGE_PREAMBLE) -> dict[str, Any]:
    """Owner decision: a quarantined item is a false positive; it goes back to ``pending`` for triage."""

    def change(row: TriageRow) -> TriageRow:
        check_quarantine_release(row.state)
        return row.model_copy(update={"state": ItemState.PENDING})

    return _owner_edit(path, item_id, change, preamble)


def upsert_processing_list(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Insert or replace rows by item id; rows not named (including hand-edited ones) are left untouched."""
    if not rows:
        raise InvalidRows("no rows given")
    parsed: list[ProcessingListRow] = []
    errors: list[str] = []
    for i, row in enumerate(rows):
        try:
            parsed.append(ProcessingListRow(**row))
        except (ValueError, ValidationError) as exc:
            errors.append(f"row {i}: {_short(exc)}")
    if errors:
        raise InvalidRows("; ".join(errors))
    result: dict[str, list[str]] = {}

    def transform(text: str | None) -> str:
        table = TableFile(text, PROCESSING_COLUMNS, key="item", preamble=PROCESSING_PREAMBLE)
        outcome: dict[str, list[str]] = {"inserted": [], "updated": [], "unchanged": []}
        for row in parsed:
            outcome[table.upsert(row.to_cells())].append(row.item_id)
        result.update(outcome)
        return table.render()

    update_text(path, transform)
    return {**result, "conflicts": find_conflicts(path)}


def read_processing_list(path: Path) -> list[ProcessingListRow]:
    table = _load(path, PROCESSING_COLUMNS, PROCESSING_PREAMBLE)
    return [ProcessingListRow.from_cells(c) for c in table.rows()]


def _short(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    return str(exc)
