"""Spec 003 US1/US2: span attributes, content gating, security flag events and metrics."""

import asyncio

import pytest

from pkpipe.telemetry import (
    REQUIRED_STEP_ATTRIBUTES,
    ContentPolicy,
    capture_content,
    flow_span,
    item_context,
    llm_span,
    record_security_flag,
    set_item_category,
    set_span_attributes,
    step_span,
    tool_span,
    traced_step,
)

CANARY = "JOURNAL-CANARY-7f3a9c"


def _run_flow(trigger="schedule"):
    with flow_span("learning", trigger=trigger, config_version="abc123"):
        for step, bpmn in (("fetch", "Task_Fetch"), ("summarize", "Task_Summarize")):
            with item_context("item-1", "tool"), step_span(step, bpmn):
                pass


def test_every_step_produces_exactly_one_span_with_all_required_attributes(otel):
    """SC-001, FR-003, US1.1."""
    _run_flow()
    steps = otel.spans("step ")
    assert sorted(s.attributes["step.id"] for s in steps) == ["fetch", "summarize"]
    for s in steps:
        for key in REQUIRED_STEP_ATTRIBUTES:
            assert key in s.attributes, f"{key} missing on {s.name}"
    fetch = next(s for s in steps if s.attributes["step.id"] == "fetch")
    assert fetch.attributes["flow.id"] == "learning"
    assert fetch.attributes["bpmn.element"] == "Task_Fetch"
    assert fetch.attributes["item.id"] == "item-1" and fetch.attributes["item.category"] == "tool"
    assert fetch.attributes["config.version"] == "abc123" and fetch.attributes["run.trigger"] == "schedule"


def test_steps_are_children_of_the_flow_span(otel):
    """US1.1: one trace per run."""
    _run_flow()
    flow = otel.spans("flow ")[0]
    assert flow.parent is None
    for s in otel.spans("step "):
        assert s.parent.span_id == flow.context.span_id and s.context.trace_id == flow.context.trace_id


def test_llm_and_tool_spans_nest_under_the_step(otel):
    """US1.1: LLM and tool calls appear as spans with flow and item attributes."""
    with flow_span("learning", trigger="manual", config_version="v1"), item_context("i9"):
        with step_span("s", "Task_S") as step:
            with llm_span("qwen3.5-9b"):
                pass
            with tool_span("fetch_url"):
                pass
    llm, tool = otel.spans("llm ")[0], otel.spans("tool ")[0]
    assert llm.parent.span_id == tool.parent.span_id == step.get_span_context().span_id
    assert llm.attributes["llm.model_name"] == "qwen3.5-9b" and tool.attributes["tool.name"] == "fetch_url"
    assert llm.attributes["flow.id"] == "learning" and tool.attributes["item.id"] == "i9"


def test_decorator_wraps_sync_and_async_steps_and_takes_item_from_kwargs(otel):
    """T003: decorator with custom attributes."""

    @traced_step("classify", "Task_Classify")
    def classify(text, *, item_id=None, item_category=None):
        return text.upper()

    @traced_step("notify", "Task_Notify")
    async def notify():
        return "ok"

    with flow_span("triage", trigger="api", config_version="c"):
        assert classify("a", item_id="n1", item_category="video") == "A"
        assert asyncio.run(notify()) == "ok"
    by_step = {s.attributes["step.id"]: s for s in otel.spans("step ")}
    assert by_step["classify"].attributes["item.id"] == "n1"
    assert by_step["classify"].attributes["item.category"] == "video"
    assert "notify" in by_step and classify.__name__ == "classify"


def test_set_item_category_updates_span_and_following_steps(otel):
    with flow_span("triage", trigger="api", config_version="c"), item_context("n2"):
        with step_span("classify", "T1"):
            set_item_category("tool")
        with step_span("route", "T2"):
            pass
    cats = {s.attributes["step.id"]: s.attributes.get("item.category") for s in otel.spans("step ")}
    assert cats == {"classify": "tool", "route": "tool"}


def test_set_span_attributes_helper_skips_none(otel):
    with otel.telemetry.tracer.start_as_current_span("x") as span:
        set_span_attributes(flow_id="f", item_category=None, bpmn_element="E")
        set_span_attributes(span, step_id="s")
    (s,) = otel.spans("x")
    assert dict(s.attributes).items() >= {"flow.id": "f", "bpmn.element": "E", "step.id": "s"}.items()
    assert "item.category" not in s.attributes


def test_failing_step_marks_error_counts_metric_and_hides_message(otel):
    """T006 step_errors_total; exception text may contain private data and must not be recorded."""
    with pytest.raises(RuntimeError), flow_span("learning", trigger="manual", config_version="c"):
        with step_span("summarize", "Task_Summarize"):
            raise RuntimeError(f"model choked on {CANARY}")
    step = otel.spans("step ")[0]
    assert step.status.status_code.name == "ERROR"
    event = next(e for e in step.events if e.name == "exception")
    assert event.attributes["exception.type"] == "RuntimeError"
    assert CANARY not in repr(step.events) and CANARY not in repr(step.status)
    assert otel.spans("flow ")[0].status.status_code.name == "ERROR"
    assert otel.metric_value("pkpipe.step_errors_total", **{"step.id": "summarize"}) == 1


def test_security_flag_is_a_span_event_and_a_metric_without_offending_text(otel):
    """FR-005 and flags_total{detector}."""
    with flow_span("learning", trigger="manual", config_version="c"):
        with step_span("guard", "Task_Guard") as span:
            record_security_flag("prompt_guard", action="quarantine", reason_code="injection_pattern")
            record_security_flag("url_filter")
    (guard,) = otel.spans("step ")
    events = [e for e in guard.events if e.name == "security.flag"]
    assert [e.attributes["detector"] for e in events] == ["prompt_guard", "url_filter"]
    assert events[0].attributes["action"] == "quarantine"
    assert events[0].attributes["reason_code"] == "injection_pattern"
    assert otel.metric_value("pkpipe.flags_total", detector="prompt_guard") == 1
    assert span is not None


def test_capture_content_is_refused_by_default(otel):
    """FR-004: default off."""
    with flow_span("learning", trigger="manual", config_version="c"), step_span("s", "T"):
        assert capture_content(prompt=f"secret {CANARY}") is False
    assert CANARY not in repr(otel.spans("step ")[0].attributes)


def test_capture_content_for_opted_in_flow_and_forced_off_for_journal(otel_factory):
    """FR-004, SC-002: opt-in flows capture; journal/context data classes never do."""
    o = otel_factory(ContentPolicy(frozenset({"learning", "journal-review"})))
    with flow_span("learning", trigger="manual", config_version="c"), step_span("s", "T"):
        assert capture_content(prompt="hello public text") is True
    with flow_span("journal-review", trigger="manual", config_version="c", data_classes=("journal",)):
        with step_span("s", "T"):
            assert capture_content(prompt=f"dear diary {CANARY}") is False
    steps = o.spans("step ")
    assert steps[0].attributes["pkpipe.content.prompt"] == "hello public text"
    assert CANARY not in repr([s.attributes for s in o.spans()])


def test_triage_metrics_items_per_category_and_backlog(otel):
    """US2.1: items_total{category}, backlog_size."""
    m = otel.telemetry.metrics
    m.item_processed("tool")
    m.item_processed("tool")
    m.item_processed("video")
    m.set_backlog(7)
    m.learning_timeout()
    m.learning_seconds.record(12.5)
    assert otel.metric_value("pkpipe.items_total", category="tool") == 2
    assert otel.metric_value("pkpipe.items_total", category="video") == 1
    assert otel.metric_value("pkpipe.backlog_size") == 7
    assert otel.metric_value("pkpipe.learning_timeouts_total") == 1
    m.set_backlog(3)
    assert otel.metric_value("pkpipe.backlog_size") == 3


def test_run_level_step_without_item_still_carries_all_seven_attributes(otel):
    """SC-001: a step outside any item (e.g. release over the whole batch) has every required key."""
    with flow_span("triage", trigger="schedule", config_version="c7"), step_span("release", "Task_Release"):
        pass
    with step_span("orphan", "Task_Orphan"):
        pass
    for s in otel.spans("step "):
        assert set(REQUIRED_STEP_ATTRIBUTES) <= set(s.attributes), s.name
    by = {s.attributes["step.id"]: s.attributes for s in otel.spans("step ")}
    assert by["release"]["flow.id"] == "triage" and by["release"]["item.id"] == ""
    assert by["orphan"]["flow.id"] == "" and by["orphan"]["run.trigger"] == ""


def test_spans_are_stamped_with_the_flow_capture_decision(otel_factory):
    """FR-004: every span (flow, step, child) carries pkpipe.content_capture; outside a flow it is False."""
    o = otel_factory(ContentPolicy(frozenset({"learning"})))
    with flow_span("learning", trigger="manual", config_version="c"), step_span("s", "T"), llm_span("m"):
        pass
    with flow_span("other", trigger="manual", config_version="c"), step_span("s", "T"):
        pass
    with o.telemetry.tracer.start_as_current_span("loose"):
        pass
    stamp = {(s.name, s.attributes.get("flow.id")): s.attributes["pkpipe.content_capture"] for s in o.spans()}
    assert stamp[("flow learning", "learning")] and stamp[("llm m", "learning")]
    assert stamp[("step s", "learning")] is True and stamp[("step s", "other")] is False
    assert stamp[("loose", None)] is False


def test_generation_metrics_tokens_per_second_and_learning_seconds(otel):
    """US2 / docs/operations.md: tokens per second per model and learning generation time."""
    m = otel.telemetry.metrics
    m.generation("qwen", completion_tokens=100, seconds=4.0)
    m.generation("qwen", completion_tokens=0, seconds=1.0)  # nothing generated: no point
    m.generation("qwen", completion_tokens=10, seconds=0.0)  # clock glitch: no division by zero
    m.learning_generation(42.0)
    (tps,) = otel.metric_points("pkpipe.tokens_per_second")
    assert tps.count == 1 and tps.sum == 25.0 and tps.attributes["llm.model_name"] == "qwen"
    (learn,) = otel.metric_points("pkpipe.learning_generation_seconds")
    assert learn.count == 1 and learn.sum == 42.0
