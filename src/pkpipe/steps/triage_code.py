"""Deterministic ``code`` functions used by the public pack's triage flows (BPMN: Process Inbox Items).

The model only suggests a category; these functions decide which items are new, whether a suggestion is a
valid category, which items are released and what is written back (constitution V and VI).
"""

from __future__ import annotations

from typing import Any

from pkpipe.domain.inbox_list import EntryKind, list_items, parse_list
from pkpipe.domain.triage import (
    MAX_PROPOSAL_ATTEMPTS,
    ItemState,
    TriageItem,
    clean_owner_note,
    is_unreviewed,
    item_id_for,
    needs_decision,
    needs_retriage,
    release,
    resolve_category,
)
from pkpipe.steps.base import StepContext, StepResult, layout_of, register_code
from pkpipe.steps.image_code import description_note

#: Suggested category of a row whose triage output was flagged (not a category, so a released item is
#: triaged again).
FLAGGED_SUGGESTION = "none"
#: Suggested category of a row that needs the owner (an image that could not be described, or that was
#: flagged): not a category either.
NO_SUGGESTION = "none"


def _kind_fields(item: dict[str, Any]) -> dict[str, Any]:
    """``kind`` of a list entry for its triage row (note items carry none: the column stays empty)."""
    kind = item.get("kind")
    return {"kind": kind} if kind and kind != "note" else {}


def _categories(ctx: StepContext) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in ctx.snapshot.data.get("categories", []) if c.get("enabled", True)}


@register_code("triage.cycle_start")
def cycle_start(ctx: StepContext) -> dict[str, Any]:
    """The start of the processing cycle on the clock port: the time budget of the image batch (spec 021)
    counts from here, so the text phase has used its part of it."""
    return {"started": ctx.ports.clock.now()}


@register_code("item.set")
def item_set(ctx: StepContext, **fields: Any) -> StepResult:
    """Set fields on the current item (for example ``state: quarantined``)."""
    return StepResult(output=fields, item=fields)


def _attempts(ctx: StepContext, item_id: str, requested: str) -> int:
    """Failed evaluations of this proposal so far (0 without the port or for another requested name)."""
    ports = getattr(ctx, "ports", None)  # the deterministic eval hands in a bare snapshot context
    port = ports.extra.get("proposal_attempts") if ports is not None else None
    return port.get(item_id, requested) if port is not None else 0


@register_code("category.proposal_failed")
def proposal_failed(ctx: StepContext) -> StepResult:
    """BPMN: Propose new category, evaluation failed (spec 022 FR-009). The item keeps its state; the
    failed evaluation is counted per item and requested name, and :func:`release_items` stops proposing
    after :data:`~pkpipe.domain.triage.MAX_PROPOSAL_ATTEMPTS` of them."""
    item = ctx.item or {}
    port = ctx.ports.extra.get("proposal_attempts")
    count = port.bump(str(item.get("id")), str(item.get("requested_category"))) if port is not None else 0
    fields = {
        "processing_status": "failed",
        "failure_reason": "the new category could not be evaluated",
        "proposal_attempts": count,
    }
    return StepResult(output=fields, item=fields)


@register_code("category.proposal_recorded")
def proposal_recorded(ctx: StepContext) -> StepResult:
    """BPMN: Propose new category, note written (spec 022). The item awaits its category; a later change
    of the override starts a fresh count."""
    port = ctx.ports.extra.get("proposal_attempts")
    if port is not None:
        port.clear(str((ctx.item or {}).get("id")))
    fields = {"state": ItemState.AWAITING_CATEGORY.value, "processing_status": "proposed"}
    return StepResult(output=fields, item=fields)


@register_code("inbox.parse_list")
def parse_inbox_list(ctx: StepContext, note: dict[str, Any]) -> dict[str, Any]:
    """BPMN: Parse INBOX list. The entries of the list file as triage items (spec 020 FR-003, FR-004).

    One item per resource (the normalised URL, the embed target or the text); a duplicate adds its raw text
    to the first item. Images are items too: ``needs_owner`` marks them for ``inbox.select_new``, which sends
    them to the image batch (spec 021), or to the owner when the config has no ``images`` section.
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


def images_enabled(ctx: Any) -> bool:
    """Whether the config has an ``images`` section (spec 021): images are described by a vision model.
    Without it an image entry is the owner's call (spec 020)."""
    data = getattr(getattr(ctx, "snapshot", None), "data", None) or {}
    return data.get("images") is not None


def _row_item(row: dict[str, Any], listed: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The item of an open triage row that is triaged again; ``None`` when a list entry is gone."""
    if (row.get("kind") or "note") == "note":
        return {"id": row["item_id"], "path": row["resource"], "resource": row["resource"]}
    if row["item_id"] in listed:
        return listed[row["item_id"]]
    # spec 031: the entry may have moved into INBOX-InProgress; the triage row still knows the resource
    return {
        "id": row["item_id"],
        "kind": row.get("kind") or "link",
        "resource": row["resource"],
        "title": "",
        "raw": "",
        "raws": [],
        "line": 0,
        "source": "list",
    }


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

    ``image_items`` are the new image entries, in list order (spec 021): the image batch describes them after
    the text items. A config without an ``images`` section keeps spec 020's behaviour: ``owner_items`` are
    images the owner categorizes, they get a row and no model call.
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
    images_on = images_enabled(ctx)
    items: list[dict[str, Any]] = []
    image_items: list[dict[str, Any]] = []
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
        if item["needs_owner"]:  # an image entry
            (image_items if images_on else owner_items).append(item)
        else:
            items.append(item)
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
            (image_items if images_on and r.get("kind") == "image" else items).append(item)
            retriage += 1
    return {
        "items": items,
        "image_items": image_items,
        "owner_items": owner_items,
        "count": len(items),
        "image_count": len(image_items),
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


def _decide_where(ctx: StepContext) -> str:
    """Where the owner decides, for the messages: the triage file or, on the ``web`` surface, the UI."""
    data = ctx.snapshot.data
    if (data.get("triage") or {}).get("surface") == "web":
        url = (data.get("ui") or {}).get("public_url")
        return f"the web UI ({url}/triage)" if url else "the web UI"
    return layout_of(ctx).triage_file


@register_code("triage.backlog")
def backlog(
    ctx: StepContext, triage: dict[str, Any], new_rows: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Notification payload for ``triage_ready``: the new suggestions plus the backlog of older items that
    still wait for the owner: neither confirmed nor overridden, or decided on a category that is no longer
    configured (``invalid``: a removed category or an unknown override). Finished items do not count."""
    valid = frozenset(_categories(ctx))
    images = ctx.resolve("images", required=False) if hasattr(ctx, "resolve") else None
    finish = ((images or {}).get("outputs") or {}).get("finish") if isinstance(images, dict) else None
    finish = finish if isinstance(finish, dict) else {}
    new_rows = [*(new_rows or []), *(finish.get("rows") or [])]  # the rows of the image batch (spec 021)
    quarantined = {r["item_id"] for r in new_rows or [] if r.get("state") == ItemState.QUARANTINED.value}
    fresh = {r["item_id"] for r in new_rows or []} - quarantined
    older = invalid = proposed_new = 0
    proposed_names: list[str] = []  # slugs of new categories the owner asks for (plain slugs only)
    proposed_rows = 0
    gave_up = 0
    gave_up_names: list[str] = []  # slugs whose proposal failed MAX_PROPOSAL_ATTEMPTS times
    cats = _categories(ctx)
    for r in (triage or {}).get("rows", []):
        try:
            state = ItemState(r.get("state", "suggested"))
        except ValueError:
            continue
        if r.get("item_id") in fresh or r.get("item_id") in quarantined:
            continue
        confirm, override = bool(r.get("confirm")), r.get("override") or None
        if override and state in (ItemState.SUGGESTED, ItemState.PENDING, ItemState.AWAITING_CATEGORY):
            kind, found = resolve_category(override, cats)
            if kind == "new" and _attempts(ctx, str(r.get("item_id")), found or "") >= MAX_PROPOSAL_ATTEMPTS:
                gave_up += 1  # the evaluation failed repeatedly: the owner has to act (spec 022 FR-009)
                if found not in gave_up_names:
                    gave_up_names.append(found or "")
                continue
            if kind == "new":  # the owner asks for a new category: a proposal, not a dead end (spec 022)
                proposed_rows += 1
                proposed_new += state is not ItemState.AWAITING_CATEGORY
                if found not in proposed_names:
                    proposed_names.append(found or "")
                continue
            if kind in ("exact", "normalised"):
                continue  # released by the next 06:00 run
            invalid += 1
            continue
        if state is ItemState.AWAITING_CATEGORY:
            invalid += 1  # the override was cleared: the owner names a category
            continue
        if is_unreviewed(state, confirm, override):
            older += 1
        elif needs_decision(state, r.get("ai_suggested_category", ""), confirm, override, valid):
            invalid += 1
    new = len(fresh)
    triage_file = _decide_where(ctx)
    if new:
        message = f"Triage ready: {new} new item(s) to confirm or override in {triage_file}."
    else:
        message = f"Triage: no new items in {triage_file}."
    first_sentence = message
    owner_new = sum(1 for r in new_rows or [] if r.get("needs_owner") and r["item_id"] in fresh)
    if owner_new:
        message += f" {owner_new} image item(s) need a category from you (write it into override)."
    report = finish.get("report") or {}
    if report:
        message += " " + str(finish.get("message") or "")
    deferred = int(report.get("deferred") or 0)
    if older:
        message += f" {older} older item(s) still wait for your decision."
    if proposed_rows:
        shown = ", ".join(proposed_names[:5]) + (", ..." if len(proposed_names) > 5 else "")
        message += (
            f" {proposed_rows} item(s) propose a new category: {shown}. The next release run (06:00) "
            f"writes a proposal note with a categories.json draft below {layout_of(ctx).proposals_dir}; "
            "add the category through a config draft and the item is released."
        )
    if gave_up:
        message += _gave_up_sentence(gave_up_names, MAX_PROPOSAL_ATTEMPTS)
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
        # the sentences after the first one (image items, deferred images, proposals, invalid decisions,
        # quarantine): plain counts, slugs and paths. The run report (spec 024) keeps them as its notes
        "attention": message[len(first_sentence) :].strip(),
        "quarantined": len(quarantined),
        "backlog": older,
        "invalid": invalid,
        "awaiting": new + older + invalid + proposed_rows + gave_up,
        "message": message,
        # New suggestions or a decision that can never be released need the owner; an unchanged backlog of
        # unreviewed items alone does not send a mail every evening (it is in the 06:00 summary).
        "notify": bool(new or invalid or quarantined or deferred or proposed_new or gave_up),
        "proposed": proposed_rows,
        "proposed_names": proposed_names,
        "proposal_gave_up": gave_up,
        "proposal_gave_up_names": gave_up_names,
        "images_deferred": deferred,
    }


def _gave_up_sentence(names: list[str], attempts: int) -> str:
    """The sentence about proposals that are no longer retried (plain slugs only)."""
    shown = ", ".join(f"'{n}'" for n in names[:5]) + (", ..." if len(names) > 5 else "")
    return (
        f" The proposal for {shown} failed {attempts} times and is not retried: change the override or "
        "add the category by hand."
    )


def _gave_up_note(gave_up: list[dict[str, Any]]) -> str:
    if not gave_up:
        return ""
    return _gave_up_sentence(sorted({g["requested_category"] for g in gave_up}), MAX_PROPOSAL_ATTEMPTS)


def _proposal_note(proposals: list[dict[str, Any]]) -> str:
    """The sentence of the daily summary about new-category proposals (plain slugs only)."""
    if not proposals:
        return ""
    names = sorted({p["requested_category"] for p in proposals})
    shown = ", ".join(names[:5]) + (", ..." if len(names) > 5 else "")
    return f" {len(proposals)} item(s) propose a new category: {shown} (proposal notes were written)."


@register_code("triage.release")
def release_items(ctx: StepContext, triage: dict[str, Any]) -> dict[str, Any]:
    """06:00 rule: only confirmed or overridden items are released; the rest stays pending.

    An override is read by :func:`~pkpipe.domain.triage.resolve_category` (spec 022): a variant of a
    configured category (``Misc``) is the configured one; a plain name that names nothing configured is a
    new-category **proposal** (``proposals``: the item keeps its state, the flow ``propose_category``
    evaluates it and marks it ``awaiting_category``, nothing is filed); anything else, and a removed
    category the owner only confirmed, goes back as pending (``unknown_category``). An item that awaits a
    category is left alone until its override names a configured one.
    """
    cats = _categories(ctx)
    layout = layout_of(ctx)
    items = []
    awaiting = 0
    originals: dict[str, ItemState] = {}
    notes: dict[str, str] = {}  # the owner's comment as the declared input owner_note (spec 023)
    for r in (triage or {}).get("rows", []):
        state = ItemState(r.get("state", "suggested"))
        override = r.get("override") or None
        if override:
            kind, found = resolve_category(override, cats)
            if kind in ("exact", "normalised"):
                override = found
        else:
            kind = "none"
        originals[r["item_id"]] = state
        if note := clean_owner_note(r.get("comment")):
            notes[r["item_id"]] = note
        if state is ItemState.AWAITING_CATEGORY:
            if kind not in ("exact", "normalised"):
                awaiting += 1  # still waits for the category: no second evaluation, no second note
                continue
            state = ItemState.SUGGESTED  # the owner named a configured category: an ordinary decided item
        items.append(
            TriageItem(
                id=r["item_id"],
                resource=r["resource"],
                ai_suggested_category=r["ai_suggested_category"],
                confirm=bool(r.get("confirm")),
                override=override,
                state=state,
                kind=r.get("kind") or "note",
            )
        )
    before = {i.id: originals[i.id] for i in items}
    released, pending = release(items)
    unknown = [i for i in released if i.effective_category not in cats]
    proposals: list[dict[str, Any]] = []
    gave_up: list[dict[str, Any]] = []
    for i in unknown:  # also a ``released`` item left over from a failed run: never stuck
        kind, slug = resolve_category(i.override, cats) if i.override else ("invalid", None)
        if kind == "new" and _attempts(ctx, i.id, slug or "") >= MAX_PROPOSAL_ATTEMPTS:
            gave_up.append({"id": i.id, "requested_category": slug})  # no third try: the owner acts
            i.state = ItemState.PENDING
        elif kind == "new":  # the owner's own words only: a removed suggestion is no proposal
            proposals.append(
                {
                    "id": i.id,
                    "resource": i.resource,
                    **({"path": i.resource} if i.kind == "note" else {"kind": i.kind, "source": "list"}),
                    "requested_category": slug,
                    **({"owner_note": notes[i.id]} if i.id in notes else {}),
                }
            )
            i.state = before[i.id]  # unchanged: the proposal flow writes ``awaiting_category``
        else:
            i.state = ItemState.PENDING
    proposed_ids = {p["id"] for p in proposals}
    gave_up_ids = {g["id"] for g in gave_up}
    released = [i for i in released if i.effective_category in cats]
    pending = pending + [i for i in unknown if i.id not in proposed_ids]  # gave-up items included
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
            **({"owner_note": notes[i.id]} if i.id in notes else {}),
        }
        for i in released
    ]
    return {
        "released": out_items,
        "proposals": proposals,
        "pending": [i.id for i in pending],
        "unknown_category": [i.id for i in unknown if i.id not in proposed_ids and i.id not in gave_up_ids],
        "proposal_gave_up": gave_up,
        "count_gave_up": len(gave_up),
        "rows": rows,
        "count_released": len(out_items),
        "count_pending": len(pending),
        "count_proposals": len(proposals),
        "count_awaiting": awaiting,
        "proposal_note": _proposal_note(proposals) + _gave_up_note(gave_up),
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
    if isinstance(item.get("description"), dict):  # an image the vision session described (spec 021)
        return {
            "path": None,
            "front_matter": {},
            "front_matter_error": None,
            "body": description_note(item["description"]),
            "conflict": False,
            "kind": "image",
        }
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
    in ``ids`` and stay. The raw text is what ``inbox_remove_entries`` matches against the file.

    Spec 031: an item whose entry already moved into INBOX-InProgress (the rows of that table, read by the
    ``read_moved`` node when it ran) is not ``missing``: its entry is archived from there when the table is
    updated after this step."""
    wanted = set(ids or [])
    table = ctx.resolve("read_moved", required=False)
    rows = table.get("rows") if isinstance(table, dict) else None
    in_table = {r.get("item_id") for r in rows or [] if isinstance(r, dict)}
    cfg = layout_of(ctx).inbox_list.to_config()
    entries = parse_list((list_note or {}).get("body", ""), cfg)
    hit = [e for e in entries if e.item_id in wanted]
    found = {e.item_id for e in hit}
    return {
        "entries": [e.raw for e in hit],
        "entry_ids": [e.item_id for e in hit],
        "count": len(hit),
        "missing": sorted(wanted - found - in_table),
        "moved_ids": sorted((wanted - found) & in_table),
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
