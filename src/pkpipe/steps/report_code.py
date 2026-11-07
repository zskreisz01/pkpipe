"""Code functions of the run report (spec 024, BPMN: Process Inbox Items / Run report).

``report.settings`` runs at the end of the triage and release flows: it reads the ``report`` config and puts
the run's own counts into one compact item for the ``run_report`` subflow. ``report.run_summary`` runs in that
subflow: it adds the state the vault is in now (the triage rows and the count of unreviewed tool notes),
builds the todos and renders the report. Nothing here calls a model, and nothing of an item's text, a URL or a
note's content is read: only states, category ids, counts and note paths.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pkpipe.domain import run_report as rr
from pkpipe.domain.triage import ItemState, is_unreviewed, needs_decision, resolve_category
from pkpipe.engine.config_models import ReportConfig
from pkpipe.steps.base import StepContext, layout_of, register_code

DECIDED_WAITING = (ItemState.SUGGESTED, ItemState.PENDING, ItemState.RELEASED)


def report_config(ctx: StepContext) -> ReportConfig:
    return ReportConfig.model_validate((ctx.snapshot.data or {}).get("report") or {})


def _categories(ctx: StepContext) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in ctx.snapshot.data.get("categories", []) if c.get("enabled", True)}


def _state(row: dict[str, Any]) -> ItemState | None:
    try:
        return ItemState(row.get("state", "suggested"))
    except ValueError:
        return None


def triage_counts(ctx: StepContext, rows: list[dict[str, Any]]) -> dict[str, int]:
    """Counts of the triage table as it is now (states and decisions only):

    ``approval``: rows the owner still has to decide (not confirmed or overridden, a decision on a category
    that is no longer configured, a proposed new category that is still not configured); ``notebooks``:
    decided items of a learning-material category that are not processed yet (the next release run
    generates the notebook and podcast)."""
    cats = _categories(ctx)
    valid = frozenset(cats)
    approval = notebooks = 0
    for r in rows or []:
        state = _state(r)
        if state is None or state not in (*DECIDED_WAITING, ItemState.AWAITING_CATEGORY):
            continue  # finished, quarantined or unknown rows are no todo
        confirm, override = bool(r.get("confirm")), (r.get("override") or None)
        suggested = str(r.get("ai_suggested_category") or "")
        if state is ItemState.AWAITING_CATEGORY:
            kind, _found = resolve_category(override, cats) if override else ("none", None)
            if kind not in ("exact", "normalised"):
                approval += 1
            continue
        if override:
            kind, found = resolve_category(override, cats)
            if kind in ("exact", "normalised"):
                override = found
            elif kind == "new":
                continue  # a new category was asked for: the next release run proposes it
            else:
                approval += 1  # no plain name at all: the owner has to write a configured category
                continue
        if needs_decision(state, suggested, confirm, override, valid):
            approval += 1
            continue
        if is_unreviewed(state, confirm, override):
            approval += 1
            continue
        if state in DECIDED_WAITING and (confirm or override):
            category = override or suggested
            if category in cats and cats[category].get("subflow") == "learning_material":
                notebooks += 1
    return {"approval": approval, "notebooks": notebooks}


def failed_to_retry(rows: list[dict[str, Any]], failed: dict[str, Any]) -> int:
    """Failed items the owner can send through the release again (spec 027): the processing list says
    ``failed`` and the triage row is ``processed`` (a quarantined item is not retryable). Ids and states
    only."""
    ids = {str(i.get("item_id")) for i in failed.get("items") or [] if isinstance(i, dict)}
    return sum(
        1 for r in rows or [] if r.get("item_id") in ids and r.get("state") == ItemState.PROCESSED.value
    )


@register_code("report.settings")
def settings(
    ctx: StepContext,
    kind: str,
    notify: bool = False,
    results: list[dict[str, Any]] | None = None,
    new_rows: list[dict[str, Any]] | None = None,
    backlog: dict[str, Any] | None = None,
    release: dict[str, Any] | None = None,
    list_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The report switch and the item of the ``run_report`` subflow.

    ``enabled`` is the ``report.enabled`` config; ``legacy_notify`` is true when the report is off and the
    run would have sent its earlier message (``notify``), so the old notification keeps working unchanged."""
    cfg = report_config(ctx)
    layout = layout_of(ctx)
    summary: dict[str, Any]
    notes: list[str]
    if kind == "triage":
        images = ctx.resolve("images", required=False)
        finish = ((images or {}).get("outputs") or {}).get("finish") if isinstance(images, dict) else None
        rep = ((finish or {}).get("report") if isinstance(finish, dict) else None) or {}
        rows = [*(new_rows or []), *(((finish or {}).get("rows") or []) if isinstance(finish, dict) else [])]
        rows = [r for r in rows if r.get("state") != ItemState.QUARANTINED.value]  # counted as quarantined
        summary = {
            "new_by_category": rr.summarize_new_rows(rows),
            "images_described": int(rep.get("described") or 0) + int(rep.get("cached") or 0),
            "images_deferred": int(rep.get("deferred") or 0),
            "quarantined": int((backlog or {}).get("quarantined") or 0),
        }
        notes = [str((backlog or {}).get("attention") or "")]
    else:
        summary = rr.summarize_results(results)
        summary["proposals"] = int((release or {}).get("count_proposals") or 0)
        # what the owner has to act on: failed proposals and the INBOX list problems (counts and slugs only)
        notes = [str((release or {}).get("proposal_note") or ""), str((list_report or {}).get("note") or "")]
    tool_folder = None
    cat = _categories(ctx).get(cfg.tool_category)
    if cat and cat.get("para_path"):
        tool_folder = layout.expand(str(cat["para_path"])).rstrip("/")
    ui = (ctx.snapshot.data or {}).get("ui") or {}
    surface = ((ctx.snapshot.data or {}).get("triage") or {}).get("surface", "vault")
    item = {
        "kind": kind,
        "summary": summary,
        "notes": [n.strip() for n in notes if n and n.strip()],
        "tool_folder": tool_folder,
        "tool_category": cfg.tool_category,
        "journal": cfg.journal_todos.enabled,
        "heading": cfg.journal_todos.heading,
        "surface": surface,
        "public_url": ui.get("public_url"),
        "triage_file": layout.triage_file,
        "processing_list": layout.processing_list,
    }
    return {"enabled": cfg.enabled, "legacy_notify": (not cfg.enabled) and bool(notify), "item": item}


def _day(ctx: StepContext) -> tuple[str, str]:
    now = datetime.fromtimestamp(ctx.ports.clock.now())
    return now.strftime("%Y-%m-%d"), now.strftime("%H:%M")


def _name(path: str) -> str:
    return path.rsplit("/", 1)[-1].removesuffix(".md")


@register_code("report.run_summary")
def run_summary(ctx: StepContext, triage: dict[str, Any] | None = None) -> dict[str, Any]:
    """The report text, the todo lines and the journal call of one run (see the module docstring)."""
    item = ctx.item or {}
    tools = ctx.resolve("tool_notes", required=False)  # absent when the count was skipped or failed
    tools = tools if isinstance(tools, dict) else {}
    day, clock = _day(ctx)
    counts = triage_counts(ctx, (triage or {}).get("rows") or [])
    tool_reads = int(tools.get("count") or 0)
    failed = ctx.resolve("failed_now", required=False)  # spec 027: absent when the count failed
    retry = failed_to_retry((triage or {}).get("rows") or [], failed if isinstance(failed, dict) else {})
    surface, public_url = item.get("surface", "vault"), item.get("public_url")
    triage_link = (
        rr.external_link("triage UI", f"{public_url}/triage")
        if surface == "web" and public_url
        else rr.wikilink(str(item.get("triage_file") or ""))
    )
    links = {
        "approval": triage_link,
        "notebooks": rr.wikilink(str(item.get("processing_list") or "")),
        "tool_reads": rr.wikilink(tools.get("newest")),
        "retry": (
            rr.external_link("failed items", f"{public_url}/triage?state=processed")
            if surface == "web" and public_url
            else rr.wikilink(str(item.get("processing_list") or ""))
        ),
    }
    todos = rr.build_todos(counts["approval"], counts["notebooks"], tool_reads, links, retry)
    notes = [n for n in (str(x).strip() for x in item.get("notes") or []) if n]
    kind = str(item.get("kind") or "process_released")
    text = rr.render_report(
        kind=kind,
        day=day,
        clock=clock,
        run_id=ctx.run.run_id,
        summary=item.get("summary") or {},
        awaiting=counts["approval"],
        todos=todos,
        ui_link=triage_link if counts["approval"] else None,
        notes=notes,
    )
    write = bool(item.get("journal"))
    summary = item.get("summary") or {}
    return {
        # numbers for the notification payload (templates and channel adapters may use them)
        "processed": int(summary.get("processed") or 0),
        "quarantined": int(summary.get("quarantined") or 0),
        "failed": int(summary.get("failed") or 0),
        "evaluations": int(summary.get("evaluations") or 0),
        "notebooks": int(summary.get("notebooks") or 0),
        "podcasts": int(summary.get("podcasts") or 0),
        "new": sum((summary.get("new_by_category") or {}).values()),
        "text": text,
        "title": rr.KIND_TITLE.get(kind, kind),
        "date": day,
        "lines": [t.line(day) for t in todos],
        "todos": {t.key: t.count for t in todos},
        "awaiting": counts["approval"],
        "write_journal": write,
        "heading": item.get("heading") or "## pkpipe todo",
    }
