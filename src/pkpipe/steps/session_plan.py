"""Category session plans at run time (spec 010, BPMN: Process Other Resource / Run Category Sessions).

* ``session_plan`` step: runs the item's category plan from ``categories.json`` session by session. Every
  session is one isolated ``llm`` or ``llm_tools`` session (fresh prompt: fixed system prompt, the category
  instruction, at most one context pack, only the declared inputs, datamarked; the run's canary in the system
  prompt) and one span. The plan is data, so a new category needs no code (SC-002).
* ``category.plan`` / ``category.note`` / ``category.agent_task`` / ``category.session_failed`` code functions
  for the flow ``other_resource``: what to run, what is stored and where (constitution V: code decides).

Untrusted-content isolation (constitution IV): raw page text never reaches this step. A session reads the
validated secure-fetch extract (``item.extract``), the INBOX note text (only sessions without tools) and the
validated output of earlier sessions (only sessions with an output schema reach a session with tools). The
step's output derives from external content, so the flow must run an ``output_guard`` over it before any write
(validated like a ``canary: true`` session).
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import PurePosixPath
from typing import Any

from pydantic import Field

from pkpipe.domain import results
from pkpipe.domain import session_plan as plans
from pkpipe.domain import tool_candidate as tcs
from pkpipe.domain.data_classes import is_private
from pkpipe.engine.config_models import Session
from pkpipe.ports import ModelError
from pkpipe.steps.base import (
    StepConfig,
    StepContext,
    StepError,
    StepExecutor,
    StepResult,
    layout_of,
    register_code,
    register_step,
)
from pkpipe.steps.learning_code import find_source_url
from pkpipe.steps.llm import LlmConfig, LlmStep, LlmToolsConfig, LlmToolsStep, guarded_session_errors

#: Item fields a session may read through ``item``.
_ITEM_FIELDS = ("id", "resource", "category")


class SessionFailed(StepError):
    """A session of the plan failed (schema error after the model's retries, model unavailable, ...). The
    message names the session and the error type only, never model output (it may derive from the web)."""


class SessionPlanConfig(StepConfig):
    category: str = Field(default="${item.category}", description="Category id (template)")
    note_text: str | None = Field(
        default=None,
        description="Template for the INBOX note text, given to the sessions that declare item.text "
        "(sessions without tools only)",
    )


def categories_of(ctx: StepContext) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in ctx.snapshot.data.get("categories", []) if c.get("enabled", True)}


def _session_config(session: Session) -> LlmConfig | LlmToolsConfig:
    base: dict[str, Any] = {
        "system": session.system,
        "instruction": session.instruction,
        "context": session.context,
        "output_schema": session.output_schema,
        "canary": True,  # every session works on content derived from the web: the output guard checks it
    }
    if session.tools:
        return LlmToolsConfig(**base, tools=session.tools)
    return LlmConfig(**base)


def _item_view(ctx: StepContext, note_text: str | None) -> dict[str, Any]:
    view = {k: (ctx.item or {}).get(k) for k in _ITEM_FIELDS if (ctx.item or {}).get(k) is not None}
    if note_text is not None:
        view["text"] = note_text
    return view


def session_inputs(
    session: Session, ctx: StepContext, results: dict[str, Any], note_text: str | None
) -> dict[str, Any]:
    """Exactly the declared inputs of one session. A skipped session counts as absent."""
    usable = {sid: out for sid, out in results.items() if not _skipped(out)}
    item = ctx.item or {}
    out: dict[str, Any] = {}
    for ref in session.inputs:
        if ref == "*":
            if item.get("extract") is not None:
                out[plans.EXTRACT_REF] = item["extract"]
            out.update(usable)
        elif ref in results:
            if ref in usable:
                out[ref] = usable[ref]
        elif ref == "item":
            out[ref] = _item_view(ctx, note_text)
        elif ref == plans.TEXT_REF:
            if note_text is not None:
                out[ref] = note_text
        elif ref == plans.OWNER_NOTE_REF and is_private(
            getattr(getattr(ctx, "flow", None), "data_classes", ())
        ):
            continue  # a flow of a private data class (journal, context, image) never receives the note
        else:
            field = ref.split(".", 1)[1]
            value = item.get(field)
            if value is None and ref == plans.EXTRACT_REF:
                raise StepError("item.extract is missing: secure fetch must run (and be clean) first")
            if value is not None:
                out[ref] = value
    return out


def _skipped(output: Any) -> bool:
    return isinstance(output, dict) and "skipped" in output and len(output) <= 2


def context_missing(session: Session, ctx: StepContext) -> bool:
    """A declared context pack the config does not carry (a private pack in a public checkout): the session is
    skipped instead of answering without the context it was written for."""
    return bool(session.context) and ctx.snapshot.asset(f"contexts/{session.context}.md") is None


@register_step
class SessionPlanStep(StepExecutor):
    """FR-001: run the category's session plan. Output: ``{category, sessions: {id: output}, skipped,
    note_session}``; a session whose context pack is missing is ``{"skipped": "context_missing"}``."""

    type = "session_plan"
    Config = SessionPlanConfig

    def check(self, node, config: SessionPlanConfig, snapshot):
        if "${" in config.category:
            return [], []
        cats = {c["id"]: c for c in snapshot.data.get("categories", [])}
        if config.category not in cats:
            return [f"node '{node.id}': category '{config.category}' does not exist"], []
        return [], []

    def check_flow(self, flow, node, config):
        # spec 009 FR-001: the output derives from external content, so an output_guard must follow
        return guarded_session_errors(flow, node, _Guarded)

    def run(self, node, config: SessionPlanConfig, ctx: StepContext) -> StepResult:
        cid = ctx.render(config.category)
        category = categories_of(ctx).get(cid)
        if category is None or not category.get("sessions"):
            raise StepError(f"category '{cid}' has no session plan")
        data = ctx.snapshot.data
        errors, _ = plans.plan_errors(
            category, servers=data.get("mcp_servers") or {}, asset=ctx.snapshot.asset, force=True
        )
        if errors:
            raise StepError("invalid session plan: " + "; ".join(errors))
        note_text = ctx.render(config.note_text) if config.note_text else None
        item_id = str((ctx.item or {}).get("id") or "") or None
        results: dict[str, Any] = {}
        skipped: list[str] = []
        for raw in category["sessions"]:
            session = Session.model_validate(raw)
            if context_missing(session, ctx):
                results[session.id] = {"skipped": "context_missing", "context": session.context}
                skipped.append(session.id)
                continue
            inputs = session_inputs(session, ctx, results, note_text)
            sub = dataclasses.replace(ctx, inputs=inputs)
            config_s = _session_config(session)
            executor = LlmToolsStep() if isinstance(config_s, LlmToolsConfig) else LlmStep()
            with ctx.ports.tracer.step(
                f"{node.id}.{session.id}", "Run category sessions", item_id=item_id, item_category=cid
            ) as span:
                span.set("pkpipe.step.type", "session")
                span.set("pkpipe.session.id", session.id)
                try:
                    result = executor.run(node, config_s, sub)
                except ModelError as exc:
                    span.fail(type(exc).__name__)
                    raise SessionFailed(f"session '{session.id}' failed: {type(exc).__name__}") from None
            results[session.id] = result.output
        return StepResult(
            output={
                "category": cid,
                "sessions": results,
                "skipped": skipped,
                "note_session": plans.note_session_id(category),
            }
        )


class _Guarded:
    """Stand-in LlmConfig for :func:`guarded_session_errors`: a plan always works on external-derived data."""

    canary = True


# -- code functions of the flow --------------------------------------------------------------------------
def image_has_description(item: dict[str, Any] | None) -> bool:
    """Spec 032: an image item with a validated description and a category folder can be its own note."""
    item = item or {}
    return (
        item.get("kind") == "image"
        and results.usable_description(item.get("description"))
        and bool(item.get("para_path"))
    )


@register_code("category.plan")
def plan(ctx: StepContext, note: dict[str, Any]) -> StepResult:
    """What ``other_resource`` runs for this item: ``status`` ``ok``, ``no_plan`` (no such category or no
    sessions), ``no_url`` (a session reads the page extract but the note names no URL) or
    ``discover`` (the same,
    and ``source_discovery`` searches for the source of this category, spec 025) or ``image_note`` (spec 032:
    an image with a description and no address, not searched for: the description becomes the note).
    ``needs_fetch`` routes through secure fetch; the URL is kept on the item as ``source_url`` (never as
    ``resource``)."""
    category = categories_of(ctx).get((ctx.item or {}).get("category") or "")
    if category is None or not category.get("sessions"):
        return StepResult(output={"status": "no_plan", "needs_fetch": False, "uses_text": False})
    info = plans.plan_info(category)
    url = find_source_url((note or {}).get("front_matter"), (note or {}).get("body", ""))
    out = {
        "status": "ok",
        "needs_fetch": info.needs_fetch,
        "uses_text": info.uses_text,
        "url": url or "",
        "sessions": list(info.sessions),
    }
    if info.needs_fetch and not url:
        # spec 025: a category that opted in searches for the source instead of failing the item
        from pkpipe.steps.discovery_code import discovery_applies

        if discovery_applies(ctx, category, ctx.item):
            out["status"] = "discover"
        else:
            out["status"] = "image_note" if image_has_description(ctx.item) else "no_url"
    return StepResult(output=out, item={"source_url": url} if url else None)


@register_code("category.note")
def note(
    ctx: StepContext, sessions: dict[str, Any], candidates: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The note to store, rendered from the validated output of the plan's note session (after the output
    guard): path below the category's PARA folder, Markdown body, front matter, the agent task of a
    new-category suggestion if a session made one, and (spec 016) the system relevance: a section of the
    note and, for a new candidate, its row (``candidates`` is the list's current content)."""
    item = ctx.item or {}
    outputs = (sessions or {}).get("sessions") or {}
    note_id = (sessions or {}).get("note_session")
    output = outputs.get(note_id)
    if not isinstance(output, dict) or _skipped(output):
        raise StepError(f"note session '{note_id}' produced no note")
    # a field named like a skipped session (fit_enterprise without its context pack) is no result of the plan
    output = {k: v for k, v in output.items() if k not in set(sessions.get("skipped") or [])}
    extract = item.get("extract") or {}
    source = item.get("source_url") or item.get("resource") or ""
    title = plans.note_title(output, extract.get("title") or f"Item {item['id']}")
    body = plans.render_note_body({**output, "title": title}, source=source or None)
    path = plans.note_path(item["para_path"], title, item["id"])
    relevance = system_relevance(ctx, outputs, tool=title, note_path=path, candidates=candidates)
    if relevance["section"]:
        body = body.rstrip("\n") + "\n\n## System relevance\n\n" + relevance["section"] + "\n"
    front = {
        "type": "resource_note",
        "source": source,
        "category": item.get("category"),
        "pkpipe_item": item["id"],
    }
    if item.get("resource") and item.get("resource") != source:
        front["inbox_note"] = item["resource"]
    inbox = PurePosixPath(item.get("path") or item.get("resource") or f"{item['id']}.md").name
    out: dict[str, Any] = {
        "path": path,
        "archive_path": f"{layout_of(ctx).archive_dir}/{item['id']}-{inbox}",
        "title": title,
        "body": body,
        "front_matter": front,
        "has_task": False,
        "has_candidate": relevance["row"] is not None,
        "candidate_rows": [relevance["row"]] if relevance["row"] else [],
        "system_relevance": relevance["reason"],
    }
    suggestion = plans.suggestion_of(outputs, set(categories_of(ctx)))
    if suggestion:
        task_path, task_body, task_front = plans.agent_task_note(
            suggestion, item_id=item["id"], note=path, folder=layout_of(ctx).agent_tasks_dir
        )
        out.update(has_task=True, task_path=task_path, task_body=task_body, task_front_matter=task_front)
    return out


@register_code("category.proposal_input")
def proposal_input(ctx: StepContext, note: dict[str, Any], requested: str) -> dict[str, Any]:
    """The text the ``misc`` evaluation reads for a new-category proposal (spec 022): the owner's requested
    name (already a slug: it came through ``resolve_category``) and the item's note, which is untrusted and
    reaches the session datamarked like any note text."""
    if not plans._SLUG.match(str(requested)):  # noqa: SLF001 - the one definition of a slug
        raise StepError("the requested category is not a slug")
    body = str((note or {}).get("body") or "")
    return {
        "text": (
            f"The owner wants to file this item under a new category named '{requested}'. Evaluate the "
            f"item and say whether it deserves that category.\n\n{body}"
        )
    }


@register_code("category.proposal_note")
def proposal_note(ctx: StepContext, sessions: dict[str, Any]) -> dict[str, Any]:
    """The proposal note of a new category (after the output guard): path below ``layout.proposals_dir``,
    body with the evaluation and a ready-to-paste ``categories.json`` entry, front matter. The versioned
    config is never written."""
    item = ctx.item or {}
    output = ((sessions or {}).get("sessions") or {}).get("evaluate")
    if not isinstance(output, dict) or _skipped(output):
        raise StepError("the evaluation session produced no answer")
    path, body, front = plans.proposal_note(
        str(item.get("requested_category") or ""),
        output,
        item_id=str(item["id"]),
        folder=layout_of(ctx).proposals_dir,
    )
    return {"path": path, "body": body, "front_matter": front}


#: What the note says about the system relevance, by the outcome of ``tool_candidate.propose``.
_RELEVANCE_TEXT = {
    "in_use": "In use: pkpipe already uses this tool.",
    "adopted": "In use: this tool was adopted for the system.",
    "rejected": "Rejected earlier as a system tool; not proposed again without new evidence.",
    "listed": "Already on the system tool candidate list.",
    "unknown_component": "Judged relevant, but the component is not part of the system context: not listed.",
}


def system_relevance(
    ctx: StepContext,
    outputs: dict[str, Any],
    *,
    tool: str,
    note_path: str,
    candidates: dict[str, Any] | None,
) -> dict[str, Any]:
    """Spec 016: what the system relevance session (``system_relevance``) means for this tool. Code decides
    (constitution V): the row is added only for a component of the system context, never for a tool already
    in use or adopted and never again for a rejected one (SC-001)."""
    output = outputs.get("system_relevance")
    if not isinstance(output, dict) or _skipped(output):
        return {"section": "", "row": None, "reason": "none"}
    context = ctx.snapshot.asset("contexts/system.md") or ""
    existing = {
        str(r.get("id")): r
        for r in (candidates or {}).get("rows") or []
        if isinstance(r, dict) and r.get("id")
    }
    name = (outputs.get("research") or {}).get("name") if isinstance(outputs.get("research"), dict) else None
    proposal = tcs.propose(
        output,
        tool=str(name or tool),
        evaluation_note=note_path,
        components=tcs.context_components(context),
        in_use=tcs.context_in_use(context),
        existing=existing,
    )
    if proposal.action == "add" and proposal.row:
        text = (
            f"Candidate for {proposal.row['component']} (effort {proposal.row['effort']}): "
            f"{proposal.row['benefit']} Listed in the system tool candidates."
        )
        return {"section": text, "row": proposal.row, "reason": "candidate"}
    return {"section": _RELEVANCE_TEXT.get(proposal.reason, ""), "row": None, "reason": proposal.reason}


_REASON = re.compile(r"[^\w .,:'()/-]+")


@register_code("category.session_failed")
def session_failed(ctx: StepContext, error: dict[str, Any] | None) -> StepResult:
    """Error branch of the plan: the item is listed as failed with the reason (the failing session and the
    error type); it is not retried every morning (a schema error repeats), the owner decides in the list."""
    message = _REASON.sub("", str((error or {}).get("message") or "session failed"))[:200]
    return StepResult(
        output={"reason": message},
        item={"processing_status": "failed", "failure_reason": message},
    )
