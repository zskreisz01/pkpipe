"""Code functions of source discovery (spec 025, BPMN: Process other resource / Find source by web search).

An item of a category with a session plan that reads the page (``item.extract``) but names no URL (an image
whose description names no address, a note without a link) used to fail with "no source URL in the note".
When ``source_discovery`` is on for its category, ``item.find_sources`` builds one search query from the
validated description or the owner's text, lets the web-query guard check it, calls the web-search tool and
keeps the URLs that pass the URL policy. ``item.next_source`` moves to the next candidate when a fetch failed.
The pages are then fetched by the ordinary ``secure_fetch`` subflow and evaluated by the category's own
sessions (config); nothing here calls a model, and neither the query nor a result text is recorded in a span,
a message or a failure reason.
"""

from __future__ import annotations

from typing import Any

from pkpipe.domain import source_discovery as sd
from pkpipe.domain.item_events import ctx_event
from pkpipe.engine.config_models import SourceDiscoveryConfig
from pkpipe.steps.base import StepContext, StepResult, register_code
from pkpipe.steps.llm import query_blocked
from pkpipe.steps.secure_fetch import url_check
from pkpipe.steps.tool import call_tool

SEARCH_TOOL = "websearch:search"
SUBFLOW = "other_resource"


def discovery_config(ctx: StepContext) -> SourceDiscoveryConfig:
    return SourceDiscoveryConfig.model_validate((ctx.snapshot.data or {}).get("source_discovery") or {})


def discovery_applies(ctx: StepContext, category: dict[str, Any] | None, item: dict[str, Any] | None) -> bool:
    """Whether an item without a URL is searched for: the switch is on, the category is listed and runs the
    ``other_resource`` subflow, and the kind of the item (image or text) is allowed."""
    cfg = discovery_config(ctx)
    if not cfg.enabled or not category or category.get("subflow") != SUBFLOW:
        return False
    if category.get("id") not in cfg.categories:
        return False
    image = (item or {}).get("kind") == "image"
    return cfg.from_images if image else cfg.from_notes


def _fail(code: str, ctx: StepContext | None = None) -> StepResult:
    describable = False
    if ctx is not None:
        ctx_event(ctx, "source", code, reason=sd.failure_reason(code))
        from pkpipe.steps.session_plan import image_has_description

        describable = image_has_description(ctx.item)  # spec 032: such an image still gets its own note
    return StepResult(
        output={"status": code, "found": 0, "reason": sd.failure_reason(code), "describable": describable}
    )


@register_code("item.find_sources")
def find_sources(ctx: StepContext, note: dict[str, Any] | None = None) -> StepResult:
    """Search for the source of the current item. ``status`` is ``ok`` (the first URL is on the item as
    ``source_url``, the others as ``source_candidates``) or a stable failure code with its ``reason`` for the
    processing list: ``no_description`` (an image the description cache does not know), ``nothing_to_search``,
    ``query_blocked``, ``no_results``, ``search_failed``."""
    cfg = discovery_config(ctx)
    item = ctx.item or {}
    description = item.get("description")
    if item.get("kind") == "image":
        if not isinstance(description, dict):
            return _fail("no_description", ctx)
        topic, text = description.get("topic"), description.get("visible_text")
    else:
        topic, text = "", (note or {}).get("body", "")
    query = sd.build_query(cfg.query_template, topic, text, cfg.max_query_chars)
    with ctx.ports.tracer.tool("source.discover") as span:
        span.set("discovery.query_chars", len(query))
        if not query:
            return _fail("nothing_to_search", ctx)
        arguments = {"query": query, "max_results": cfg.max_results}
        if query_blocked(ctx, SEARCH_TOOL, arguments):
            span.set("discovery.refused", "blocked_term")
            return _fail("query_blocked", ctx)
        try:
            results = call_tool(ctx, SEARCH_TOOL, arguments)
        except Exception as exc:  # noqa: BLE001 - a failed search is that item's failure, with a fixed reason
            span.set("discovery.error", type(exc).__name__)
            return _fail("search_failed", ctx)
        urls, tally = sd.pick_candidates(
            results, lambda u: bool(url_check(ctx, url=u).get("allowed")), cfg.max_candidates
        )
        span.set("discovery.results", tally["results"])
        span.set("discovery.candidates", len(urls))
    if not urls:
        return _fail("no_results", ctx)
    ctx_event(ctx, "source", "ok", found=len(urls), results=tally["results"], urls=urls)
    return StepResult(
        output={"status": "ok", "found": len(urls), "results": tally["results"], "dropped": tally["dropped"]},
        item=sd.discovered_fields(urls),
    )


@register_code("item.next_source")
def next_source(ctx: StepContext) -> StepResult:
    """After a failed fetch of a found page: the next candidate becomes ``source_url``; ``exhausted`` when
    none is left (the item then fails like any item whose page could not be fetched)."""
    waiting = [u for u in (ctx.item or {}).get("source_candidates") or [] if isinstance(u, str)]
    if not waiting:
        return StepResult(output={"status": "exhausted"})
    return StepResult(
        output={"status": "ok", "remaining": len(waiting) - 1},
        item={"source_url": waiting[0], "source_candidates": waiting[1:]},
    )
