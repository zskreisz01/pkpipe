"""Triage file and processing list as structured rows (spec 004 US2, FR-001, T004-T006)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

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


def _normalise_triage_update(update: dict[str, Any], existing: dict[str, str] | None) -> TriageRow:
    if existing is not None:
        merged = TriageRow.from_cells(existing).model_dump()
        merged.update({k: v for k, v in update.items() if k in TriageRow.model_fields})
        return TriageRow(**merged)
    data = {k: v for k, v in update.items() if k in TriageRow.model_fields}
    if "item_id" not in data and data.get("resource"):
        data["item_id"] = item_id_for(data["resource"])
    return TriageRow(**data)


def update_triage(path: Path, updates: list[dict[str, Any]]) -> dict[str, Any]:
    """Upsert rows by item id.

    Provided fields overwrite, omitted fields keep the file's current value, so the owner's confirm/override
    ticks survive agent updates. The whole batch is validated before anything is written.
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
                update = {**update, "item_id": item_id_for(update["resource"])}
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
