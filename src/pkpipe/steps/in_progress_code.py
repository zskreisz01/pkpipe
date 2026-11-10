"""Code functions of the table of unfinished items (spec 031, BPMN: Move unfinished entries).

``in_progress.plan`` decides from our own records which items are unfinished, which are closed (their row
leaves the table and the kept entry is archived) and which are open again (the kept entry goes back to the
INBOX list). It builds the facts the situation session reads. ``in_progress.row`` and
``in_progress.fallback_row`` turn the session's answer (or its absence) into the row; ``in_progress.collect``
puts the rows together for the vault tool.

The facts are ids, codes, numbers and the fixed sentences of the processing list and the item event log. They
never hold page, note or image text; the resource (an address, an embed target, the owner's own line) is
named as data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pkpipe.domain import in_progress as ip
from pkpipe.domain.item_events import describe, emit
from pkpipe.steps.base import StepContext, StepResult, register_code

NOTABLE = ("verdict", "source", "quarantined", "held", "in_progress")
MAX_RESOURCE_FACT = 300


def _settings(ctx: StepContext) -> dict[str, Any]:
    triage = ctx.snapshot.section("triage", {}) or {}
    cfg = triage.get("in_progress") or {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "max_rows": int(cfg.get("max_rows_per_run", 40)),
        "max_events": int(cfg.get("max_events", 10)),
        "retry_max": int(triage.get("retry_max", 3)),
    }


def _review_facts(review: dict[str, Any] | None) -> dict[str, Any] | None:
    evidence = (review or {}).get("evidence") or {}
    if not evidence:
        return None
    return {
        "reading": evidence.get("reading"),
        "threshold": evidence.get("threshold"),
        "models": [
            {
                "model": m.get("model"),
                "kind": m.get("kind"),
                "top_score": m.get("max_score"),
                "label": m.get("label"),
            }
            for m in evidence.get("members") or []
        ],
    }


def _notable(events: list[dict[str, Any]], limit: int) -> list[str]:
    """The last events worth telling the model: failures, verdicts, source searches, quarantine and hold."""
    keep = [
        e
        for e in events
        if e.get("kind") in NOTABLE or (e.get("kind") == "step" and e.get("outcome") == "failed")
    ]
    return [describe(e)[:220] for e in keep[-limit:]]


def _facts(
    row: dict[str, Any],
    proc: dict[str, Any],
    *,
    held: dict[str, Any] | None,
    retries: int,
    events: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    state, status = str(row.get("state") or ""), str(proc.get("status") or "")
    reason = str(proc.get("link") or "") if status in ip.OPEN_STATUSES else ""
    code = ip.cause_code(state, status, reason, held=held is not None)
    return {
        "kind": row.get("kind") or "note",
        "resource": str(row.get("resource") or "")[:MAX_RESOURCE_FACT],
        "category": row.get("override") or row.get("ai_suggested_category") or "",
        "triage_state": state,
        "processing_status": status,
        "failure_reason": " ".join(reason.split())[:200],
        "cause": code,
        "owner_retries": retries,
        "retry_limit": cfg["retry_max"],
        "held_review": _review_facts(held),
        "events": _notable(events, cfg["max_events"]),
    }


@register_code("in_progress.plan")
def plan(
    ctx: StepContext,
    triage: dict[str, Any] | None = None,
    processing: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Which rows the table has after this run, and which of them the model has to describe."""
    cfg = _settings(ctx)
    empty = {"enabled": cfg["enabled"], "has_work": False, "describe": [], "carried": [], "archive_ids": []}
    if not cfg["enabled"]:
        return {**empty, "restore_ids": [], "unfinished": 0, "count_describe": 0}
    rows = [r for r in (triage or {}).get("rows") or [] if isinstance(r, dict) and r.get("item_id")]
    by_status = {i["item_id"]: i for i in (processing or {}).get("items") or [] if i.get("item_id")}
    table = {r["item_id"]: r for r in (progress or {}).get("rows") or [] if r.get("item_id")}
    reviews, retries_port = ctx.ports.extra.get("classifier_reviews"), ctx.ports.extra.get("item_retries")
    events_port = ctx.ports.extra.get("item_events")
    try:
        held = {i: r for i, r in (reviews.held() if reviews else {}).items() if not r.get("waived")}
    except Exception:  # noqa: BLE001 - a broken helper table must not block the table
        held = {}
    try:
        retries = retries_port.retry_counts() if retries_port else {}
    except Exception:  # noqa: BLE001
        retries = {}
    today = datetime.fromtimestamp(ctx.ports.clock.now(), UTC).date().isoformat()
    last_run = ctx.run.run_id[:8]
    archive_ids, restore_ids, describe_items, carried = [], [], [], []
    unfinished = 0
    for row in rows:
        item_id, state = str(row["item_id"]), str(row.get("state") or "")
        proc = by_status.get(item_id, {})
        status = str(proc.get("status") or "")
        in_table = item_id in table
        if ip.is_closed(state, status):
            if in_table:
                archive_ids.append(item_id)
            continue
        if not ip.is_unfinished(state, status, held=item_id in held):
            if in_table and state in ("pending", "suggested", "awaiting_category"):
                restore_ids.append(item_id)  # released from the quarantine: its entry goes back to the list
            continue
        unfinished += 1
        try:
            events = events_port.events_of(item_id, 200) if events_port else []
        except Exception:  # noqa: BLE001
            events = []
        facts = _facts(
            row,
            proc,
            held=held.get(item_id),
            retries=int(retries.get(item_id, 0)),
            events=events,
            cfg=cfg,
        )
        digest = ip.facts_hash(facts)
        base = {
            "item_id": item_id,
            "kind": facts["kind"],
            "resource": facts["resource"],
            "category": facts["category"],
            "state": state,
            "since": today,
            "last_run": last_run,
            "move_entry": (row.get("kind") or "note") != "note",
        }
        prior = table.get(item_id)
        if prior and prior.get("facts") == digest and prior.get("situation"):
            carried.append(
                {
                    **base,
                    "situation": prior["situation"],
                    "cause": prior.get("cause") or facts["cause"],
                    "next_step": prior.get("next_step") or "",
                    "facts": digest,
                    "last_run": prior.get("last_run") or last_run,
                    "outcome": "carried",
                }
            )
            continue
        describe_items.append(
            {
                "id": item_id,
                "row": {**base, "facts": digest},
                "facts": facts,
                "facts_text": ip.facts_text(facts),
            }
        )
    # beyond the cap: the fixed sentence now, the model next run (the facts hash stays blank)
    overflow = describe_items[cfg["max_rows"] :]
    describe_items = describe_items[: cfg["max_rows"]]
    for item in overflow:
        carried.append({**item["row"], **ip.fallback_row(item["facts"]), "facts": "", "outcome": "fallback"})
    return {
        "enabled": True,
        "has_work": bool(describe_items or carried or archive_ids or restore_ids),
        "describe": describe_items,
        "carried": carried,
        "archive_ids": archive_ids,
        "restore_ids": restore_ids,
        "unfinished": unfinished,
        "count_describe": len(describe_items),
    }


@register_code("in_progress.row")
def row(ctx: StepContext, answer: Any = None) -> StepResult:
    """The row of the item from the session's answer (cut to its limits)."""
    item = ctx.item or {}
    facts = item.get("facts") or {}
    body = {**item.get("row", {}), **ip.clean_answer(answer, facts), "outcome": "described"}
    return StepResult(output=body, item={"progress_row": body})


@register_code("in_progress.fallback_row")
def fallback_row(ctx: StepContext) -> StepResult:
    """The model could not answer: the fixed sentence built from the codes, so the table never blocks."""
    item = ctx.item or {}
    body = {**item.get("row", {}), **ip.fallback_row(item.get("facts") or {}), "outcome": "fallback"}
    return StepResult(output=body, item={"progress_row": body})


@register_code("in_progress.collect")
def collect(
    ctx: StepContext, plan: dict[str, Any], described: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The rows for the vault tool: carried rows, described rows, and a fallback row for an item whose
    description step failed outright. Each row gets an event in the item's history."""
    rows = list((plan or {}).get("carried") or [])
    done = set()
    for r in described or []:
        item = r.get("item") or {}
        progress_row = item.get("progress_row")
        if r.get("status") == "done" and isinstance(progress_row, dict):
            rows.append(progress_row)
            done.add(progress_row["item_id"])
    for want in (plan or {}).get("describe") or []:
        if want["id"] not in done:
            rows.append({**want["row"], **ip.fallback_row(want["facts"]), "outcome": "fallback"})
    port = ctx.ports.extra.get("item_events")
    for r in rows:
        emit(
            port,
            r["item_id"],
            "in_progress",
            run_id=ctx.run.run_id,
            flow_id=ctx.flow.id if ctx.flow else None,
            outcome=r.get("outcome"),
            codes={"cause": r.get("cause"), "state": r.get("state")},
        )
    for item_id in (plan or {}).get("archive_ids") or []:
        emit(port, item_id, "in_progress", run_id=ctx.run.run_id, outcome="closed")
    for item_id in (plan or {}).get("restore_ids") or []:
        emit(port, item_id, "in_progress", run_id=ctx.run.run_id, outcome="reopened")
    clean = [{k: v for k, v in r.items() if k != "outcome"} for r in rows]
    return {
        "rows": clean,
        "archive_ids": list((plan or {}).get("archive_ids") or []),
        "restore_ids": list((plan or {}).get("restore_ids") or []),
        "count": len(clean),
        "fallback": sum(1 for r in rows if r.get("outcome") == "fallback"),
    }
