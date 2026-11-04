"""Deterministic ``code`` functions of the self-improvement flows (spec 015, BPMN: Self-Improvement Cycle).

The sessions only propose: these functions decide which journal passage becomes a backlog item, which quote
counts as evidence, which verdict is recorded and what is written back (constitution V and VI).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from pkpipe.domain import improvement as imp
from pkpipe.steps.base import StepContext, StepError, StepResult, layout_of, register_code


def _today(ctx: StepContext, value: Any = None) -> date:
    """The run's day: ``date`` argument, else ``input.date``, else the clock's local date."""
    for candidate in (value, ctx.resolve("input.date", required=False)):
        if candidate:
            parsed = imp.parse_date(candidate)
            if parsed is None:
                raise StepError(f"date '{candidate}' is not YYYY-MM-DD")
            return parsed
    return datetime.fromtimestamp(ctx.ports.clock.now()).date()


def _journal_path(ctx: StepContext, day: date, folder: str | None = None) -> str:
    """Path of a journal day: the layout's ``journal_pattern``, or ``<folder>/<yyyy-MM-dd>.md`` when a flow
    names a ``folder`` explicitly (the pre-020 spelling)."""
    return imp.journal_path(folder, day) if folder else layout_of(ctx).journal_path(day)


@register_code("improvement.locate_journal")
def locate_journal(ctx: StepContext, folder: str | None = None, date: str | None = None) -> dict[str, Any]:
    day = _today(ctx, date)
    return {"path": _journal_path(ctx, day, folder), "date": day.isoformat()}


@register_code("improvement.section")
def section(
    ctx: StepContext, note: dict[str, Any], path: str, date: str, heading: str = "Improvement"
) -> dict[str, Any]:
    """The journal's improvement section. Nothing found: the flow ends without calling a model, so a journal
    without such a section never leaves the vault process."""
    text = imp.extract_section((note or {}).get("body", ""), heading)
    return {"found": bool(text), "text": text, "path": path, "date": date}


@register_code("improvement.new_rows")
def new_rows(
    ctx: StepContext,
    section: dict[str, Any],
    extracted: dict[str, Any],
    backlog: dict[str, Any],
    max_items: int = 10,
) -> dict[str, Any]:
    """Backlog rows (always ``proposed``) for the statements that are verbatim in the journal section."""
    existing = {r.get("id") for r in (backlog or {}).get("rows", [])}
    day = imp.parse_date(section.get("date")) or _today(ctx)
    grounded = imp.ground_items(
        (extracted or {}).get("items", []),
        section=section.get("text", ""),
        source_note=section["path"],
        day=day,
        existing_ids=existing,
        max_items=max_items,
    )
    return {"rows": grounded.items, "count": len(grounded.items), "dropped": grounded.dropped}


# -- weekly review ------------------------------------------------------------------------------------------
@register_code("improvement.select_review")
def select_review(
    ctx: StepContext,
    backlog: dict[str, Any],
    journal_folder: str | None = None,
    proposals_folder: str | None = None,
    max_days: int = 28,
    date: str | None = None,
) -> dict[str, Any]:
    """Active items to review, the journal days to read, and kept system items still without a proposal."""
    today = _today(ctx, date)
    rows = (backlog or {}).get("rows", [])
    chosen = imp.select_review(rows, today, max_days=max_days)
    proposals = [
        {
            "id": r["id"],
            "path": imp.proposal_path(proposals_folder or layout_of(ctx).proposals_dir, r["id"]),
            "body": imp.proposal_note(r),
        }
        for r in rows
        if r.get("status") == imp.Status.KEPT.value
        and r.get("area") == imp.Area.SYSTEM.value
        and not r.get("proposal")
    ]
    return {
        "today": today.isoformat(),
        "items": chosen["items"],
        "journal_paths": [_journal_path(ctx, d, journal_folder) for d in chosen["days"]],
        "proposals": proposals,
        "count": len(chosen["items"]),
    }


@register_code("improvement.attach_journals")
def attach_journals(
    ctx: StepContext, items: list[dict[str, Any]], notes: dict[str, Any], today: str, max_days: int = 28
) -> dict[str, Any]:
    day = imp.parse_date(today) or _today(ctx)
    # the day of a journal note follows from the layout's pattern (a day's file name need not be its date)
    days = {
        layout_of(ctx).journal_path(day - timedelta(days=n)): (day - timedelta(days=n)).isoformat()
        for n in range(max_days + 1)
    }
    by_day = {}
    for n in (notes or {}).get("notes", []):
        path = str(n.get("path", ""))
        stem = path.rsplit("/", 1)[-1].removesuffix(".md")
        by_day[days.get(path, stem)] = n.get("body", "")
    out = imp.attach_journals(items or [], by_day, day, max_days=max_days)
    return {"items": out, "count": len(out)}


@register_code("improvement.check_evidence")
def check_evidence(
    ctx: StepContext, evidence: dict[str, Any], journal_days: list[dict[str, str]]
) -> dict[str, Any]:
    return imp.verify_evidence((evidence or {}).get("evidence", []), journal_days or [])


def _review_result(fields: dict[str, str]) -> StepResult:
    # the journal text is not needed after this point: keep the checkpointed item small
    return StepResult(output=fields, item={"review": fields, "journal": None, "journal_days": None})


@register_code("improvement.propose")
def propose(ctx: StepContext, verdict: dict[str, Any], evidence: list[dict[str, str]]) -> StepResult:
    try:
        return _review_result(imp.verdict_fields(verdict, evidence))
    except ValueError as exc:
        raise StepError(f"the session returned no valid verdict: {exc}") from None


@register_code("improvement.no_evidence")
def no_evidence(ctx: StepContext) -> StepResult:
    return _review_result(imp.verdict_fields(None, []))


@register_code("improvement.review_rows")
def review_rows(ctx: StepContext, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Weekly proposals as backlog rows; an item whose review failed is left unchanged."""
    rows, failed = [], 0
    counts = {"keep": 0, "adjust": 0, "drop": 0}
    for r in results or []:
        item = r.get("item") or {}
        review = item.get("review")
        if r.get("status") != "done" or not isinstance(review, dict):
            failed += 1
            continue
        rows.append({"id": item["id"], **review})
        counts[review["verdict"]] += 1
    return {"rows": rows, "count": len(rows), "failed": failed, **counts}


@register_code("improvement.proposal_rows")
def proposal_rows(ctx: StepContext, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Link every written system-change proposal note from its backlog row. A note that already exists (an
    earlier run wrote it but could not link it, or the owner made it) is linked as it is, never replaced."""
    rows, failed = [], 0
    for r in results or []:
        item = r.get("item") or {}
        exists = "note exists" in str((r.get("error") or {}).get("message", "")).lower()
        if (r.get("status") != "done" and not exists) or not item.get("path"):
            failed += 1
            continue
        rows.append({"id": item["id"], "proposal": item["path"]})
    return {"rows": rows, "count": len(rows), "failed": failed}


@register_code("improvement.summary")
def summary(
    ctx: StepContext, review: dict[str, Any], proposals: dict[str, Any], location: str = ""
) -> dict[str, Any]:
    """The owner's notification: counts and where to look, never the private statements."""
    total = review["count"] + proposals["count"]
    parts = []
    if review["count"]:
        parts.append(
            f"{review['count']} item(s) with evidence and a verdict proposal "
            f"({review['keep']} keep, {review['adjust']} adjust, {review['drop']} drop)"
        )
    if proposals["count"]:
        parts.append(f"{proposals['count']} system change proposal(s) waiting for the eval gate")
    message = "Weekly improvement review ready: " + "; ".join(parts) + "." if parts else ""
    if message and location:
        message += f" Confirm or change the status in {location}."
    return {"total": total, "message": message, "reviewed": review["count"], "proposals": proposals["count"]}
