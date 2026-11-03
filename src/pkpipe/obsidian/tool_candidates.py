"""The system tool candidate list as a Markdown table (spec 016, FR-001, SC-001).

``read_tool_candidates`` / ``update_tool_candidates`` follow the improvement backlog's pattern: the owner
edits the table in Obsidian, the pipeline adds rows through code that enforces the rules it may not bypass:

* a new row is always ``candidate`` and carries no owner field (reason, ADR, A/B, evidence);
* an existing row is never changed by the pipeline (not even to add a second evaluation of the same tool):
  a rejected tool therefore stays rejected, an adopted one stays adopted (SC-001);
* :func:`owner_set_status` is the owner channel (CLI, in-process: not a tool of the vault server): it applies
  :func:`pkpipe.domain.tool_candidate.check_transition`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pkpipe.domain import tool_candidate as tc

from .atomic import update_text
from .errors import InvalidRows
from .lists import _short, find_conflicts
from .tables import TableFile

COLUMNS = (
    "status",
    "id",
    "tool",
    "evaluation",
    "component",
    "benefit",
    "effort",
    "reason",
    "adr",
    "ab",
    "evidence",
)
PREAMBLE = (
    "# System tool candidates\n\n"
    "Tools whose evaluation says they could improve pkpipe itself. The agent adds candidates; you decide: "
    "candidate, investigating, trial, adopted or rejected (a rejection needs a **reason**). Adopt only after "
    "a spike that beats the current component in the A/B on the eval suite, with an ADR "
    "(`pkpipe candidates ab` and `pkpipe candidates set`). A rejected tool is not proposed again without "
    "new **evidence**.\n\n"
)


def _link(path: str) -> str:
    return f"[[{path.removesuffix('.md')}]]" if path else ""


def _unlink(cell: str) -> str:
    cell = cell.strip()
    return cell[2:-2].split("|")[0] if cell.startswith("[[") and cell.endswith("]]") else cell


def row_to_cells(row: tc.ToolCandidate) -> dict[str, str]:
    return {
        "status": row.status.value,
        "id": row.id,
        "tool": row.tool,
        "evaluation": _link(row.evaluation_note),
        "component": row.component,
        "benefit": row.benefit,
        "effort": row.effort.value,
        "reason": row.reason,
        "adr": row.adr,
        "ab": row.ab,
        "evidence": row.evidence,
    }


def row_from_cells(cells: dict[str, str]) -> tc.ToolCandidate:
    note = _unlink(cells["evaluation"])
    return tc.ToolCandidate(
        id=cells["id"],
        tool=cells["tool"],
        evaluation_note=note if not note or note.endswith(".md") else note + ".md",
        component=cells["component"],
        benefit=cells["benefit"],
        effort=cells["effort"],
        status=cells["status"] or tc.Status.CANDIDATE,
        reason=cells["reason"],
        adr=cells["adr"],
        ab=cells["ab"],
        evidence=cells["evidence"],
    )


def _load(path: Path) -> TableFile:
    text = path.read_text(encoding="utf-8") if path.exists() else None
    return TableFile(text, COLUMNS, key="id", preamble=PREAMBLE)


def read_tool_candidates(path: Path) -> dict[str, Any]:
    """Rows, unreadable rows, rules the owner's hand edits break (``violations``) and sync conflicts."""
    table = _load(path)
    rows: list[dict[str, Any]] = []
    invalid = [{"line": n, "text": t} for n, t in table.invalid]
    violations: list[dict[str, str]] = []
    for cells in table.rows():
        try:
            row = row_from_cells(cells)
        except (ValueError, ValidationError):
            invalid.append({"line": 0, "text": f"candidate {cells['id']}: unreadable row"})
            continue
        rows.append(row.model_dump(mode="json"))
        violations += [{"id": row.id, "problem": p} for p in tc.row_violations(row)]
    return {"rows": rows, "invalid": invalid, "violations": violations, "conflicts": find_conflicts(path)}


def _new_row(update: dict[str, Any]) -> tc.ToolCandidate:
    data = {k: v for k, v in update.items() if k in tc.ToolCandidate.model_fields}
    if "status" in data and str(data["status"]).strip().lower() != tc.Status.CANDIDATE.value:
        raise ValueError("new candidates start in status 'candidate' (the owner decides the rest)")
    if any(str(data.get(f) or "").strip() for f in tc.OWNER_FIELDS if f != "status"):
        raise ValueError("reason, adr, ab and evidence belong to the owner")
    for field in ("tool", "evaluation_note", "component", "benefit", "effort"):
        if not str(data.get(field) or "").strip():
            raise ValueError(f"{field} is required for a new candidate")
    data["tool"] = tc.clean(data["tool"], 120)
    expected = tc.candidate_id(data["tool"])
    if "id" in data and str(data["id"]).strip() != expected:
        raise ValueError("id of a new candidate must be derived from the tool name")
    data["id"], data["status"] = expected, tc.Status.CANDIDATE
    for field in ("component", "benefit", "evaluation_note"):
        data[field] = tc.clean(data[field], 300)
    return tc.ToolCandidate(**data)


def update_tool_candidates(path: Path, updates: list[dict[str, Any]]) -> dict[str, Any]:
    """Add candidates. Rows that exist (whatever their status) are left exactly as they are and reported as
    ``unchanged``; the batch is validated before any write (all or nothing)."""
    if not updates:
        raise InvalidRows("no rows given")
    result: dict[str, list[str]] = {}

    def transform(text: str | None) -> str:
        table = TableFile(text, COLUMNS, key="id", preamble=PREAMBLE)
        outcome: dict[str, list[str]] = {"inserted": [], "unchanged": []}
        errors: list[str] = []
        for i, update in enumerate(updates):
            try:
                row = _new_row(update)
            except (ValueError, ValidationError) as exc:
                errors.append(f"row {i}: {_short(exc)}")
                continue
            if table.get(row.id) is not None:
                outcome["unchanged"].append(row.id)
                continue
            table.upsert(row_to_cells(row))
            outcome["inserted"].append(row.id)
        if errors:
            raise InvalidRows("; ".join(errors))
        result.update(outcome)
        return table.render()

    update_text(path, transform)
    return {**result, "conflicts": find_conflicts(path)}


def owner_set_status(
    path: Path,
    candidate: str,
    status: tc.Status,
    *,
    reason: str = "",
    evidence: str = "",
    adr: str = "",
    adr_exists: bool = False,
    ab: tc.AbEvidence | None = None,
) -> dict[str, Any]:
    """The owner changes the status of one candidate (``candidate`` is its id or tool name). Raises
    ``ValueError`` with the reason when :func:`~pkpipe.domain.tool_candidate.check_transition` refuses."""
    key = tc.candidate_id(candidate)
    result: dict[str, Any] = {}

    def transform(text: str | None) -> str:
        table = TableFile(text, COLUMNS, key="id", preamble=PREAMBLE)
        cells = table.get(key)
        if cells is None:
            raise ValueError(f"no candidate '{candidate}' in the list")
        row = row_from_cells(cells)
        reopening = row.status is tc.Status.REJECTED and status is tc.Status.CANDIDATE
        why = tc.check_transition(
            row,
            status,
            reason=reason or row.reason,
            evidence=evidence,
            adr_exists=adr_exists,
            ab=ab,
        )
        if why:
            raise ValueError(why)
        updated = row.model_copy(update={"status": status})
        if status is tc.Status.REJECTED:
            updated.reason = tc.clean(reason or row.reason, 300)
        if reopening:
            updated.evidence = tc.clean(evidence, 300)
            updated.reason = ""
        if adr:
            updated.adr = tc.clean(adr, 200)
        if ab is not None and status in (tc.Status.TRIAL, tc.Status.ADOPTED):
            updated.ab = tc.clean(ab.summary or ab.verdict, 200)
        table.upsert(row_to_cells(updated))
        result.update(id=key, old=row.status.value, new=status.value)
        return table.render()

    update_text(path, transform)
    return result
