"""Code functions of the pipeline overview note (spec 033, BPMN: Refresh the pipeline overview note).

``pipeline.build`` reads what the flow just read (triage table, processing list, unfinished-items table, the
INBOX list) and the helper tables through ports, builds the overview with the same assembler the UI uses and
renders ``Pipeline.md``. ``pipeline.write_failed`` records that the vault write failed; a failing overview
never fails the run it is attached to.

Only ids, codes, URLs and the short sentences of the unfinished-items table are in the note; no page, note or
image text (see ``pkpipe.domain.pipeline``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pkpipe.domain import pipeline as pl
from pkpipe.domain.inbox_list import InboxListConfig, list_items, parse_list
from pkpipe.domain.layout import VaultLayout
from pkpipe.steps.base import StepContext, StepResult, layout_of, register_code
from pkpipe.steps.results_code import result_folders

log = logging.getLogger("pkpipe.pipeline")


def _list_config(ctx: StepContext) -> InboxListConfig:
    data = (getattr(ctx.snapshot, "data", None) or {}).get("layout") or {}
    try:
        return VaultLayout.model_validate(data).inbox_list.to_config()
    except Exception:  # noqa: BLE001 - the defaults parse the usual list
        return InboxListConfig()


def _inbox_ids(ctx: StepContext, note: Any) -> list[str] | None:
    if not isinstance(note, dict):
        return None
    body = note.get("body")
    if body is None and note.get("text") is None:
        return None
    text = str(body if body is not None else note.get("text") or "")
    try:
        return [i.id for i in list_items(parse_list(text, _list_config(ctx)))]
    except Exception:  # noqa: BLE001
        return None


@register_code("pipeline.build")
def build(
    ctx: StepContext,
    triage: str = "read_triage",
    processing: str = "read_processing",
    progress: str = "read_progress",
    inbox: str = "read_inbox",
) -> StepResult:
    """The overview note text, or ``enabled: false`` when ``triage.pipeline.enabled`` is off.

    The arguments name the nodes whose results are read (a node that did not run, like the INBOX list of a
    layout without a list file, is simply absent)."""
    triage, processing, progress, inbox = (
        ctx.resolve(ref, required=False) for ref in (triage, processing, progress, inbox)
    )
    cfg = (ctx.snapshot.section("triage", {}) or {}).get("pipeline") or {}
    if not cfg.get("enabled", False):
        return StepResult(output={"enabled": False})
    layout = layout_of(ctx)
    extra = ctx.ports.extra
    triage_cfg = ctx.snapshot.section("triage", {}) or {}
    ui = ctx.snapshot.section("ui", {}) or {}
    next_runs_fn = extra.get("next_runs")
    now = datetime.fromtimestamp(ctx.ports.clock.now(), UTC)
    schedules = ctx.snapshot.section("schedules", []) or []
    results = [{"folder": folder, "index": f"{folder}/_Index.md"} for folder in result_folders(ctx)]
    ov = pl.assemble(
        triage_rows=[r for r in (triage or {}).get("rows") or [] if isinstance(r, dict) and r.get("item_id")],
        processing_items=(processing or {}).get("items") or [],
        progress_rows=(progress or {}).get("rows") or [],
        stats=extra.get("pipeline_stats"),
        run_history=extra.get("run_history"),
        learning_jobs=extra.get("learning_jobs"),
        next_runs=next_runs_fn(schedules, now) if callable(next_runs_fn) else [],
        inbox_ids=_inbox_ids(ctx, inbox),
        retry_max=int(triage_cfg.get("retry_max", 3)),
        where=pl.where_map(layout, results),
        now=now,
        recent_days=int(cfg.get("recent_days", pl.DEFAULT_RECENT_DAYS)),
    )
    text = pl.render_markdown(ov, ui_url=ui.get("public_url"))
    return StepResult(output={"enabled": True, "text": text, "items": ov["funnel"]["total"]})


@register_code("pipeline.write_failed")
def write_failed(ctx: StepContext) -> StepResult:
    """The vault write of the note failed: logged, never raised (the run it belongs to goes on)."""
    log.warning("the pipeline overview note was not written (run %s)", ctx.run.run_id[:8])
    return StepResult(output={"written": False})
