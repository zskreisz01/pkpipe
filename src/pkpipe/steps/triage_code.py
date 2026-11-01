"""Deterministic ``code`` functions used by the public pack's triage flows (BPMN: Process Inbox Items).

The model only suggests a category; these functions decide which items are new, whether a suggestion is a
valid category, which items are released and what is written back (constitution V and VI).
"""

from __future__ import annotations

from typing import Any

from pkpipe.domain.triage import ItemState, TriageItem, is_unreviewed, item_id_for, needs_retriage, release
from pkpipe.steps.base import StepContext, StepResult, register_code

MANAGED_FILES = ("Inbox-Triage.md", "Manual-Resource-Processing-List.md")


def _categories(ctx: StepContext) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in ctx.snapshot.data.get("categories", []) if c.get("enabled", True)}


@register_code("item.set")
def item_set(ctx: StepContext, **fields: Any) -> StepResult:
    """Set fields on the current item (for example ``state: quarantined``)."""
    return StepResult(output=fields, item=fields)


@register_code("inbox.select_new")
def select_new(
    ctx: StepContext,
    listing: dict[str, Any],
    triage: dict[str, Any],
    folder: str = "0-INBOX",
    exclude: list[str] | None = None,
) -> dict[str, Any]:
    """INBOX notes that are not yet in the triage file. Item id = hash of the note path (the resource)."""
    skip = set(MANAGED_FILES) | set(exclude or [])
    rows = (triage or {}).get("rows", [])
    known = {r.get("item_id") for r in rows}
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in (listing or {}).get("notes", []):
        if name in skip:
            continue
        resource = f"{folder.rstrip('/')}/{name}"
        item_id = item_id_for(resource)
        if item_id not in known and item_id not in seen:  # the same resource is one item (id = hash)
            seen.add(item_id)
            items.append({"id": item_id, "path": resource, "resource": resource})
    # Re-triage: the suggested category was removed from the config while the item was still open.
    valid = frozenset(_categories(ctx))
    retriage = 0
    for r in rows:
        try:
            state = ItemState(r.get("state", "suggested"))
        except ValueError:
            continue
        if r.get("item_id") in seen or not r.get("resource"):
            continue
        if needs_retriage(state, r.get("ai_suggested_category", ""), r.get("override") or None, valid):
            seen.add(r["item_id"])
            items.append({"id": r["item_id"], "path": r["resource"], "resource": r["resource"]})
            retriage += 1
    return {"items": items, "count": len(items), "retriage": retriage}


@register_code("triage.category_menu")
def category_menu(ctx: StepContext) -> dict[str, Any]:
    """The categories a triage session may choose from: id, label and hint only (no session plans)."""
    menu = [
        {"id": c["id"], "label": c.get("label", c["id"]), "hint": c.get("triage_hint", "")}
        for c in _categories(ctx).values()
    ]
    return {"categories": menu}


@register_code("triage.check_category")
def check_category(ctx: StepContext, suggestion: dict[str, Any], fallback: str = "misc") -> StepResult:
    """Accept the model's category only if it is a configured category; otherwise use ``fallback``."""
    cats = _categories(ctx)
    proposed = (suggestion or {}).get("category")
    valid = isinstance(proposed, str) and proposed in cats
    category = proposed if valid else (fallback if fallback in cats else sorted(cats)[0])
    out = {"category": category, "proposed": proposed, "valid": valid}
    return StepResult(output=out, item={"category": category})


@register_code("triage.suggested_rows")
def suggested_rows(ctx: StepContext, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Triage rows (state ``suggested``) for every successfully categorized item."""
    rows, failed = [], []
    for r in results or []:
        item = r.get("item") or {}
        if r.get("status") != "done" or not item.get("category"):
            failed.append(item.get("id"))
            continue
        rows.append(
            {
                "item_id": item["id"],
                "resource": item["resource"],
                "ai_suggested_category": item["category"],
                "state": ItemState.SUGGESTED.value,
            }
        )
    return {"rows": rows, "count": len(rows), "failed": failed}


@register_code("triage.backlog")
def backlog(
    ctx: StepContext, triage: dict[str, Any], new_rows: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Notification payload for ``triage_ready``: the new suggestions plus the backlog of older items that
    still wait for the owner (neither confirmed nor overridden, not processed or quarantined)."""
    fresh = {r["item_id"] for r in new_rows or []}
    older = 0
    for r in (triage or {}).get("rows", []):
        try:
            state = ItemState(r.get("state", "suggested"))
        except ValueError:
            continue
        if r.get("item_id") not in fresh and is_unreviewed(
            state, bool(r.get("confirm")), r.get("override") or None
        ):
            older += 1
    new = len(fresh)
    message = f"Triage ready: {new} new item(s) to confirm or override in 0-INBOX/Inbox-Triage.md."
    if older:
        message += f" {older} older item(s) still wait for your decision."
    return {"new": new, "backlog": older, "awaiting": new + older, "message": message}


@register_code("triage.release")
def release_items(ctx: StepContext, triage: dict[str, Any]) -> dict[str, Any]:
    """06:00 rule: only confirmed or overridden items are released; the rest stays pending.

    An override naming an unknown category is not released (the owner gets it back as pending).
    """
    cats = _categories(ctx)
    items = [
        TriageItem(
            id=r["item_id"],
            resource=r["resource"],
            ai_suggested_category=r["ai_suggested_category"],
            confirm=bool(r.get("confirm")),
            override=r.get("override") or None,
            state=ItemState(r.get("state", "suggested")),
        )
        for r in (triage or {}).get("rows", [])
    ]
    before = {i.id: i.state for i in items}
    released, pending = release(items)
    unknown = [i for i in released if i.effective_category not in cats]
    for i in unknown:
        i.state = ItemState.PENDING if before[i.id] is not ItemState.RELEASED else ItemState.RELEASED
    released = [i for i in released if i.effective_category in cats]
    pending = pending + [i for i in unknown if i.state is ItemState.PENDING]
    rows = [
        {"item_id": i.id, "state": i.state.value} for i in released + pending if before[i.id] is not i.state
    ]
    out_items = [
        {
            "id": i.id,
            "resource": i.resource,
            "path": i.resource,
            "category": i.effective_category,
            "subflow": cats[i.effective_category]["subflow"],
            "para_path": cats[i.effective_category]["para_path"],
        }
        for i in released
    ]
    return {
        "released": out_items,
        "pending": [i.id for i in pending],
        "unknown_category": [i.id for i in unknown],
        "rows": rows,
        "count_released": len(out_items),
        "count_pending": len(pending),
    }


@register_code("triage.finalize")
def finalize(ctx: StepContext, results: list[dict[str, Any]]) -> dict[str, Any]:
    """After processing: processed or quarantined state per item, processing-list rows, summary counts.

    A failed item stays ``released`` and is picked up again by the next run.
    """
    triage_rows, processing_rows = [], []
    counts = {"processed": 0, "quarantined": 0, "failed": 0}
    for r in results or []:
        item = r.get("item") or {}
        if r.get("status") != "done":
            counts["failed"] += 1
            continue
        if item.get("flagged") or item.get("state") == ItemState.QUARANTINED.value:
            triage_rows.append({"item_id": item["id"], "state": ItemState.QUARANTINED.value})
            counts["quarantined"] += 1
            continue
        triage_rows.append({"item_id": item["id"], "state": ItemState.PROCESSED.value})
        counts["processed"] += 1
        processing_rows.append(
            {
                "item_id": item["id"],
                "category": item.get("category", ""),
                "obsidian_link": item.get("note") or item.get("resource", ""),
                "nb_id": item.get("nb_id"),
                "link": item.get("link"),
                "status": item.get("processing_status", "done"),
            }
        )
    return {
        "triage_rows": triage_rows,
        "processing_rows": processing_rows,
        "has_triage_rows": bool(triage_rows),
        "has_processing_rows": bool(processing_rows),
        **counts,
    }
