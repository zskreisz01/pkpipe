"""Triage file and processing list as structured rows (spec 004 US2, FR-001, T004-T006)."""

from __future__ import annotations

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
from .models import ProcessingListRow, TriageRow, item_id_for
from .policy import is_conflict_name
from .tables import TableFile

TRIAGE_COLUMNS = ("confirm", "item", "resource", "suggested", "override", "state")
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


def _load(path: Path, columns: tuple[str, ...], preamble: str) -> TableFile:
    text = path.read_text(encoding="utf-8") if path.exists() else None
    return TableFile(text, columns, key="item", preamble=preamble)


def read_triage(path: Path) -> dict[str, Any]:
    table = _load(path, TRIAGE_COLUMNS, TRIAGE_PREAMBLE)
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


def _normalise_triage_update(update: dict[str, Any], existing: dict[str, str] | None) -> TriageRow:
    """Merge ``update`` into the file's row and enforce the rules the pipeline may not bypass.

    Constitution VI: ``confirm`` and ``override`` are the owner's decisions, made in Obsidian; the pipeline
    may repeat their current value but never change it, and new rows always start ``suggested``.
    Constitution V: state changes follow :data:`pkpipe.domain.triage.TRANSITIONS`, so ``released`` needs a
    confirm tick or an override.
    """
    data = {k: v for k, v in update.items() if k in TriageRow.model_fields}
    if existing is None:
        if data.get("confirm") or (isinstance(data.get("override"), str) and data["override"].strip()):
            raise ValueError("confirm and override are set by the owner, not by the pipeline")
        if "state" in data and str(data["state"]).strip().lower() != ItemState.SUGGESTED.value:
            raise ValueError("new triage rows start in state 'suggested'")
        if not data.get("resource"):
            raise ValueError("resource is required for a new row")
        expected = item_id_for(data["resource"])
        if "item_id" in data and str(data["item_id"]).strip() != expected:
            raise ValueError("item_id of a new row must be the hash of its resource")
        data["item_id"] = expected
        return TriageRow(**data)
    current = TriageRow.from_cells(existing)
    candidate = TriageRow(**{**current.model_dump(), **data})
    for field in OWNER_FIELDS:
        if field in data and getattr(candidate, field) != getattr(current, field):
            raise ValueError(f"{field} is set by the owner, not by the pipeline")
    if "resource" in data and candidate.resource != current.resource:
        raise ValueError("the resource of an existing row cannot change (the item id is its hash)")
    reason = check_transition(
        current.state, candidate.state, owner_decided=current.confirm or current.override is not None
    )
    if reason:
        raise ValueError(reason)
    return candidate


def update_triage(path: Path, updates: list[dict[str, Any]]) -> dict[str, Any]:
    """Upsert rows by item id.

    Provided fields overwrite, omitted fields keep the file's current value. ``confirm`` and ``override``
    can only be changed by the owner in the file, new rows start ``suggested`` with ``item_id`` = hash of the
    resource, and state changes follow the domain transition rules. The whole batch is validated before
    anything is written.
    """
    if not updates:
        raise InvalidRows("no rows given")
    result: dict[str, list[str]] = {}

    def transform(text: str | None) -> str:
        table = TableFile(text, TRIAGE_COLUMNS, key="item", preamble=TRIAGE_PREAMBLE)
        outcome: dict[str, list[str]] = {"inserted": [], "updated": [], "unchanged": []}
        errors: list[str] = []
        for i, update in enumerate(updates):
            if "item_id" not in update and update.get("resource"):
                update = {**update, "item_id": item_id_for(str(update["resource"]))}
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
        result.update(outcome)
        return table.render()

    update_text(path, transform)
    return {**result, "conflicts": find_conflicts(path)}


def _owner_edit(path: Path, item_id: str, change) -> dict[str, Any]:
    """Apply ``change(current_row) -> new_row`` to one existing triage row under the file lock."""
    result: dict[str, Any] = {}

    def transform(text: str | None) -> str:
        table = TableFile(text, TRIAGE_COLUMNS, key="item", preamble=TRIAGE_PREAMBLE)
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
    categories: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """The owner's confirm tick and/or override category for one item, written to the triage file.

    This is the only writer of ``confirm`` / ``override`` besides the owner's own edit in Obsidian. It is
    reachable from the owner channels (UI, authenticated app MCP) and never from the vault MCP server, which
    sessions may use. Decision rules: :func:`pkpipe.domain.triage.check_owner_decision`.
    """

    def change(row: TriageRow) -> TriageRow:
        check_owner_decision(row.state, confirm=confirm, override=override, categories=categories)
        data = row.model_dump()
        if confirm is not None:
            data["confirm"] = confirm
        if override is not None:
            data["override"] = override.strip() or None
        return TriageRow(**data)

    return _owner_edit(path, item_id, change)


def release_quarantine(path: Path, item_id: str) -> dict[str, Any]:
    """Owner decision: a quarantined item is a false positive; it goes back to ``pending`` for triage."""

    def change(row: TriageRow) -> TriageRow:
        check_quarantine_release(row.state)
        return row.model_copy(update={"state": ItemState.PENDING})

    return _owner_edit(path, item_id, change)


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
