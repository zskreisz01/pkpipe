"""Deterministic ``code`` functions used by the public pack's triage flows (BPMN: Process Inbox Items).

The model only suggests a category; these functions decide which items are new, whether a suggestion is a
valid category, which items are released and what is written back (constitution V and VI).
"""

from __future__ import annotations

from typing import Any

from pkpipe.domain.inbox_list import EntryKind, list_items, parse_list
from pkpipe.domain.triage import (
    ItemState,
    TriageItem,
    is_unreviewed,
    item_id_for,
    needs_decision,
    needs_retriage,
    release,
)
from pkpipe.steps.base import StepContext, StepResult, layout_of, register_code

#: Suggested category of a row whose triage output was flagged (not a category, so a released item is
#: triaged again).
FLAGGED_SUGGESTION = "none"
#: Suggested category of a row that needs the owner (an image entry): not a category either.
NO_SUGGESTION = "none"


def _kind_fields(item: dict[str, Any]) -> dict[str, Any]:
    """``kind`` of a list entry for its triage row (note items carry none: the column stays empty)."""
    kind = item.get("kind")
    return {"kind": kind} if kind and kind != "note" else {}


def _categories(ctx: StepContext) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in ctx.snapshot.data.get("categories", []) if c.get("enabled", True)}


@register_code("item.set")
def item_set(ctx: StepContext, **fields: Any) -> StepResult:
    """Set fields on the current item (for example ``state: quarantined``)."""
    return StepResult(output=fields, item=fields)


@register_code("inbox.parse_list")
def parse_inbox_list(ctx: StepContext, note: dict[str, Any]) -> dict[str, Any]:
    """BPMN: Parse INBOX list. The entries of the list file as triage items (spec 020 FR-003, FR-004).

    One item per resource (the normalised URL, the embed target or the text); a duplicate adds its raw text
    to the first item. Images are items too, but the owner decides their category (``needs_owner``).
    """
    cfg = layout_of(ctx).inbox_list.to_config()
    entries = parse_list((note or {}).get("body", ""), cfg)
    items: list[dict[str, object]] = []
    by_kind: dict[str, int] = {}
    for item in list_items(entries):
        d = item.as_dict()
        d["needs_owner"] = item.kind is EntryKind.IMAGE
        items.append(d)
        by_kind[item.kind.value] = by_kind.get(item.kind.value, 0) + 1
    return {"items": items, "count": len(items), "entries": len(entries), "kinds": by_kind}


def _row_item(row: dict[str, Any], listed: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The item of an open triage row that is triaged again; ``None`` when a list entry is gone."""
    if (row.get("kind") or "note") == "note":
        return {"id": row["item_id"], "path": row["resource"], "resource": row["resource"]}
    return listed.get(row["item_id"])


@register_code("inbox.select_new")
def select_new(
    ctx: StepContext,
    listing: dict[str, Any],
    triage: dict[str, Any],
    folder: str | None = None,
    exclude: list[str] | None = None,
    also_notes: bool = False,
) -> dict[str, Any]:
    """Items that are not yet in the triage file (spec 020 FR-006: both sources, the layout chooses).

    * notes source (default): INBOX notes; item id = hash of the note path (the resource);
    * list source: when the flow ran ``parse_list`` (the layout names an ``inbox_list_file``), the list
      entries are the items and the notes of the folder are ignored (``also_notes`` adds them).

    ``owner_items`` are items whose category the owner decides (images): they get a row, no model call.
    """
    layout = layout_of(ctx)
    folder = folder or layout.inbox_dir
    skip = set(layout.managed_names) | set(exclude or [])
    rows = (triage or {}).get("rows", [])
    known = {r.get("item_id") for r in rows}
    resolve = getattr(ctx, "resolve", None)  # the eval harness passes a bare context
    parsed = resolve("parse_list", required=False) if resolve else None
    list_mode = isinstance(parsed, dict)
    listed = {i["id"]: i for i in parsed["items"]} if isinstance(parsed, dict) else {}
    items: list[dict[str, Any]] = []
    owner_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not list_mode or also_notes:
        for name in (listing or {}).get("notes", []):
            if name in skip:
                continue
            resource = f"{folder.rstrip('/')}/{name}"
            item_id = item_id_for(resource)
            if item_id not in known and item_id not in seen:  # the same resource is one item (id = hash)
                seen.add(item_id)
                items.append({"id": item_id, "path": resource, "resource": resource})
    for item in listed.values():
        if item["id"] in known or item["id"] in seen:
            continue
        seen.add(item["id"])
        (owner_items if item["needs_owner"] else items).append(item)
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
        suggested, override = r.get("ai_suggested_category", ""), r.get("override") or None
        if needs_retriage(
            state, suggested, bool(r.get("confirm")), override, valid, bool(r.get("needs_owner"))
        ):
            item = _row_item(r, listed)
            if item is None:
                continue
            seen.add(r["item_id"])
            items.append(item)
            retriage += 1
    return {
        "items": items,
        "owner_items": owner_items,
        "count": len(items),
        "retriage": retriage,
        "needs_owner": len(owner_items),
    }


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
def suggested_rows(
    ctx: StepContext, results: list[dict[str, Any]], owner_items: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Triage rows (state ``suggested``) for every successfully categorized item, and rows in state
    ``quarantined`` for items the output guard flagged (spec 009: their suggestion is not trusted, so the
    suggested category is ``none``; after the owner's release the item is triaged again)."""
    rows, failed = [], []
    quarantined = 0
    for owner_item in owner_items or []:  # spec 020: an image is the owner's call, no model has seen it
        rows.append(
            {
                "item_id": owner_item["id"],
                "resource": owner_item["resource"],
                "ai_suggested_category": NO_SUGGESTION,
                "state": ItemState.SUGGESTED.value,
                "kind": owner_item.get("kind", "image"),
                "needs_owner": True,
            }
        )
    for r in results or []:
        item = r.get("item") or {}
        if r.get("status") == "done" and item.get("flagged"):
            if item.get("state") != ItemState.QUARANTINED.value:  # flagged but not handled: never trusted
                failed.append(item.get("id"))
                continue
            quarantined += 1
            rows.append(
                {
                    "item_id": item["id"],
                    "resource": item["resource"],
                    "ai_suggested_category": FLAGGED_SUGGESTION,
                    "state": ItemState.QUARANTINED.value,
                    **_kind_fields(item),
                }
            )
            continue
        if r.get("status") != "done" or not item.get("category"):
            failed.append(item.get("id"))
            continue
        rows.append(
            {
                "item_id": item["id"],
                "resource": item["resource"],
                "ai_suggested_category": item["category"],
                "state": ItemState.SUGGESTED.value,
                **_kind_fields(item),
            }
        )
    return {"rows": rows, "count": len(rows), "failed": failed, "quarantined": quarantined}


@register_code("triage.backlog")
def backlog(
    ctx: StepContext, triage: dict[str, Any], new_rows: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Notification payload for ``triage_ready``: the new suggestions plus the backlog of older items that
    still wait for the owner: neither confirmed nor overridden, or decided on a category that is no longer
    configured (``invalid``: a removed category or an unknown override). Finished items do not count."""
    valid = frozenset(_categories(ctx))
    quarantined = {r["item_id"] for r in new_rows or [] if r.get("state") == ItemState.QUARANTINED.value}
    fresh = {r["item_id"] for r in new_rows or []} - quarantined
    older = invalid = 0
    for r in (triage or {}).get("rows", []):
        try:
            state = ItemState(r.get("state", "suggested"))
        except ValueError:
            continue
        if r.get("item_id") in fresh or r.get("item_id") in quarantined:
            continue
        confirm, override = bool(r.get("confirm")), r.get("override") or None
        if is_unreviewed(state, confirm, override):
            older += 1
        elif needs_decision(state, r.get("ai_suggested_category", ""), confirm, override, valid):
            invalid += 1
    new = len(fresh)
    triage_file = layout_of(ctx).triage_file
    if new:
        message = f"Triage ready: {new} new item(s) to confirm or override in {triage_file}."
    else:
        message = f"Triage: no new items in {triage_file}."
    owner_new = sum(1 for r in new_rows or [] if r.get("needs_owner") and r["item_id"] in fresh)
    if owner_new:
        message += f" {owner_new} image item(s) need a category from you (write it into override)."
    if older:
        message += f" {older} older item(s) still wait for your decision."
    if invalid:
        message += (
            f" {invalid} item(s) name a category that is no longer configured: write a configured category"
            " into override."
        )
    if quarantined:
        message += (
            f" {len(quarantined)} new item(s) were quarantined by the security checks (see the alerts)."
        )
    return {
        "new": new,
        "quarantined": len(quarantined),
        "backlog": older,
        "invalid": invalid,
        "awaiting": new + older + invalid,
        "message": message,
        # New suggestions or a decision that can never be released need the owner; an unchanged backlog of
        # unreviewed items alone does not send a mail every evening (it is in the 06:00 summary).
        "notify": bool(new or invalid or quarantined),
    }


@register_code("triage.release")
def release_items(ctx: StepContext, triage: dict[str, Any]) -> dict[str, Any]:
    """06:00 rule: only confirmed or overridden items are released; the rest stays pending.

    An override naming an unknown category is not released (the owner gets it back as pending).
    """
    cats = _categories(ctx)
    layout = layout_of(ctx)
    items = [
        TriageItem(
            id=r["item_id"],
            resource=r["resource"],
            ai_suggested_category=r["ai_suggested_category"],
            confirm=bool(r.get("confirm")),
            override=r.get("override") or None,
            state=ItemState(r.get("state", "suggested")),
            kind=r.get("kind") or "note",
        )
        for r in (triage or {}).get("rows", [])
    ]
    before = {i.id: i.state for i in items}
    released, pending = release(items)
    unknown = [i for i in released if i.effective_category not in cats]
    for i in unknown:  # also a ``released`` item left over from a failed run: back to the owner, never stuck
        i.state = ItemState.PENDING
    released = [i for i in released if i.effective_category in cats]
    pending = pending + unknown
    rows = [
        {"item_id": i.id, "state": i.state.value} for i in released + pending if before[i.id] is not i.state
    ]
    out_items = [
        {
            "id": i.id,
            "resource": i.resource,
            # an entry of the list file is not a note: no path, the flows work from the resource (spec 020)
            **({"path": i.resource} if i.kind == "note" else {"kind": i.kind, "source": "list"}),
            "category": i.effective_category,
            "subflow": cats[i.effective_category]["subflow"],
            "para_path": layout.expand(cats[i.effective_category]["para_path"]),
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
    triage_rows, processing_rows, consumed = [], [], []
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
        status = item.get("processing_status", "done")
        if item.get("source") == "list" and status != "failed":
            consumed.append(
                item["id"]
            )  # its entry leaves the list file (spec 020 FR-005); a failed one stays
        link = item.get("link")
        if status == "failed" and not link and item.get("failure_reason"):
            link = " ".join(str(item["failure_reason"]).split())[:200]  # the list has no reason column
        processing_rows.append(
            {
                "item_id": item["id"],
                "category": item.get("category", ""),
                "obsidian_link": item.get("note") or item.get("resource", ""),
                "nb_id": item.get("nb_id"),
                "link": link,
                "status": status,
            }
        )
    return {
        "triage_rows": triage_rows,
        "processing_rows": processing_rows,
        "has_triage_rows": bool(triage_rows),
        "has_processing_rows": bool(processing_rows),
        "consume_ids": consumed,
        "has_consumed": bool(consumed),
        **counts,
    }


@register_code("inbox.source_note")
def source_note(ctx: StepContext) -> dict[str, Any]:
    """The note a processing or categorizing flow works from (BPMN: Read INBOX item).

    An INBOX note was read by the flow's ``fetch_note`` step: it is passed on as it is. An entry of the list
    file has no note: the "note" is built from the item (its title and resource, no tracking parameters),
    which is all the sessions get to see of it (untrusted text stays data).
    """
    note = ctx.resolve("fetch_note", required=False)
    if isinstance(note, dict):
        return note
    item = ctx.item or {}
    resource = str(item.get("resource", ""))
    title = str(item.get("title") or "")
    kind = item.get("kind") or "text"
    url = resource if resource.startswith(("http://", "https://")) else ""
    body = "\n\n".join(p for p in (title if title != resource else "", resource) if p)
    return {
        "path": None,
        "front_matter": {"source": url} if url else {},
        "front_matter_error": None,
        "body": body,
        "conflict": False,
        "kind": kind,
    }


@register_code("inbox.consume")
def consume(ctx: StepContext, list_note: dict[str, Any], ids: list[str]) -> dict[str, Any]:
    """The raw entries of processed items, read from the list as it is now (spec 020 FR-005).

    All duplicates of a processed resource are consumed with it. Entries the owner added meanwhile are not
    in ``ids`` and stay. The raw text is what ``inbox_remove_entries`` matches against the file."""
    wanted = set(ids or [])
    cfg = layout_of(ctx).inbox_list.to_config()
    entries = parse_list((list_note or {}).get("body", ""), cfg)
    hit = [e for e in entries if e.item_id in wanted]
    found = {e.item_id for e in hit}
    return {
        "entries": [e.raw for e in hit],
        "entry_ids": [e.item_id for e in hit],
        "count": len(hit),
        "missing": sorted(wanted - found),
    }


@register_code("inbox.report")
def report(ctx: StepContext) -> dict[str, Any]:
    """What happened to the list entries of the processed items (spec 020 US3 scenario 3).

    An item whose entry is no longer in the list (the owner removed or edited it meanwhile) is reported as
    ``not_found`` instead of being dropped silently; so is a failed removal. Reads the outputs of
    ``consume`` and ``remove_entries`` when those steps ran."""
    consume_out = ctx.resolve("consume", required=False)
    removed = ctx.resolve("remove_entries", required=False)
    consume_out = consume_out if isinstance(consume_out, dict) else {}
    ids = list(consume_out.get("missing") or [])
    raws = list(consume_out.get("entries") or [])
    entry_ids = list(consume_out.get("entry_ids") or [])
    failed = False
    if isinstance(removed, dict) and "not_found" in removed:
        by_raw = dict(zip(raws, entry_ids, strict=False))
        ids += [by_raw.get(r, r) for r in removed.get("not_found") or []]
    elif raws:  # the removal step ran into an error: nothing is known to be consumed
        failed = True
    not_found = sorted(set(ids))
    parts = []
    if not_found:
        parts.append(f"{len(not_found)} processed item(s) no longer in the INBOX list (not_found)")
    if failed:
        parts.append("removing the entries from the INBOX list failed; they stay in the list")
    return {
        "not_found": not_found,
        "count_not_found": len(not_found),
        "remove_failed": failed,
        "note": (" " + "; ".join(parts) + ".") if parts else "",
    }
