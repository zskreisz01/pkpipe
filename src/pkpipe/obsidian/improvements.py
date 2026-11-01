"""The improvement backlog as a Markdown table and batch note reading (spec 015, FR-001).

``read_improvements`` / ``update_improvements`` follow the triage file's pattern: the owner edits the table
in Obsidian, the pipeline upserts rows through code that enforces the rules it may not bypass:

* a new row is always ``proposed``, its id is the hash of source note and statement, and it carries no
  review fields (the pipeline proposes, the owner decides);
* on an existing row the pipeline may write only the review fields (evidence, verdict, reason, next
  experiment, proposal link); every other field, the status first of all, is the owner's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pkpipe.domain.improvement import AGENT_FIELDS, ImprovementItem, Status, item_id_for

from .atomic import update_text
from .errors import InvalidRows
from .lists import _short, find_conflicts
from .tables import TableFile

IMPROVEMENT_COLUMNS = (
    "status",
    "id",
    "date",
    "area",
    "type",
    "statement",
    "criterion",
    "review",
    "source",
    "evidence",
    "verdict",
    "reason",
    "next",
    "proposal",
)
IMPROVEMENT_PREAMBLE = (
    "# Improvement backlog\n\n"
    "The agent proposes, you decide: change **status** (proposed, active, reviewing, kept, adjusted, "
    "dropped) yourself. Evidence, verdict, reason and next are the agent's weekly proposal.\n\n"
)
_OWNER_FIELDS = tuple(f for f in ImprovementItem.model_fields if f not in AGENT_FIELDS)


def item_to_cells(item: ImprovementItem) -> dict[str, str]:
    return {
        "status": item.status.value,
        "id": item.id,
        "date": item.date.isoformat(),
        "area": item.area.value,
        "type": item.type.value,
        "statement": item.statement,
        "criterion": item.success_criterion,
        "review": item.review_date.isoformat() if item.review_date else "",
        "source": f"[[{item.source_note.removesuffix('.md')}]]",
        "evidence": item.evidence,
        "verdict": item.verdict.value if item.verdict else "",
        "reason": item.reason,
        "next": item.next_experiment,
        "proposal": f"[[{item.proposal.removesuffix('.md')}]]" if item.proposal else "",
    }


def _unlink(cell: str) -> str:
    cell = cell.strip()
    return cell[2:-2].split("|")[0] if cell.startswith("[[") and cell.endswith("]]") else cell


def item_from_cells(cells: dict[str, str]) -> ImprovementItem:
    source = _unlink(cells["source"])
    proposal = _unlink(cells["proposal"])
    return ImprovementItem(
        id=cells["id"],
        source_note=source if source.endswith(".md") or not source else source + ".md",
        date=cells["date"],
        statement=cells["statement"],
        area=cells["area"].strip().lower(),
        type=cells["type"].strip().lower(),
        success_criterion=cells["criterion"],
        review_date=cells["review"],
        status=cells["status"].strip().lower() or Status.PROPOSED,
        evidence=cells["evidence"],
        verdict=cells["verdict"].strip().lower(),
        reason=cells["reason"],
        next_experiment=cells["next"],
        proposal=proposal if not proposal or proposal.endswith(".md") else proposal + ".md",
    )


def _load(path: Path) -> TableFile:
    text = path.read_text(encoding="utf-8") if path.exists() else None
    return TableFile(text, IMPROVEMENT_COLUMNS, key="id", preamble=IMPROVEMENT_PREAMBLE)


def read_improvements(path: Path) -> dict[str, Any]:
    table = _load(path)
    rows: list[dict[str, Any]] = []
    invalid = [{"line": n, "text": t} for n, t in table.invalid]
    for cells in table.rows():
        try:
            rows.append(item_from_cells(cells).model_dump(mode="json", exclude_none=True))
        except (ValueError, ValidationError):
            invalid.append({"line": 0, "text": f"item {cells['id']}: unreadable row"})
    return {"rows": rows, "invalid": invalid, "conflicts": find_conflicts(path)}


def _normalise_update(update: dict[str, Any], existing: dict[str, str] | None) -> ImprovementItem:
    data = {k: v for k, v in update.items() if k in ImprovementItem.model_fields}
    if existing is None:
        if "status" in data and str(data["status"]).strip().lower() != Status.PROPOSED.value:
            raise ValueError("new improvement items start in status 'proposed' (the owner decides the rest)")
        if any(str(data.get(f) or "").strip() for f in AGENT_FIELDS):
            raise ValueError("evidence, verdict, reason, next and proposal belong to the weekly review")
        if not str(data.get("source_note", "")).strip() or not str(data.get("statement", "")).strip():
            raise ValueError("source_note and statement are required for a new item")
        expected = item_id_for(data["source_note"], data["statement"])
        if "id" in data and str(data["id"]).strip() != expected:
            raise ValueError("id of a new item must be the hash of its source note and statement")
        data["id"], data["status"] = expected, Status.PROPOSED
        return ImprovementItem(**data)
    current = item_from_cells(existing)
    candidate = ImprovementItem(**{**current.model_dump(), **data})
    for field in _OWNER_FIELDS:
        if field in data and getattr(candidate, field) != getattr(current, field):
            raise ValueError(f"{field} is set by the owner, not by the pipeline")
    return candidate


def update_improvements(path: Path, updates: list[dict[str, Any]]) -> dict[str, Any]:
    """Upsert rows by id; omitted fields keep the file's value; the batch is validated before any write."""
    if not updates:
        raise InvalidRows("no rows given")
    result: dict[str, list[str]] = {}

    def transform(text: str | None) -> str:
        table = TableFile(text, IMPROVEMENT_COLUMNS, key="id", preamble=IMPROVEMENT_PREAMBLE)
        outcome: dict[str, list[str]] = {"inserted": [], "updated": [], "unchanged": []}
        errors: list[str] = []
        for i, update in enumerate(updates):
            if "id" not in update and update.get("source_note") and update.get("statement"):
                update = {**update, "id": item_id_for(str(update["source_note"]), str(update["statement"]))}
            key = str(update.get("id", "")).strip()
            existing = table.get(key) if key else None
            try:
                row = _normalise_update(update, existing)
            except (ValueError, ValidationError) as exc:
                errors.append(f"row {i}: {_short(exc)}")
                continue
            outcome[table.upsert(item_to_cells(row))].append(row.id)
        if errors:
            raise InvalidRows("; ".join(errors))
        result.update(outcome)
        return table.render()

    update_text(path, transform)
    return {**result, "conflicts": find_conflicts(path)}
