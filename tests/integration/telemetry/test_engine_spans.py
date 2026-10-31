"""Spec 005 + constitution X: engine runs emit OpenTelemetry spans through the real tracer adapter."""

from __future__ import annotations

from opentelemetry.trace import StatusCode

from pkpipe.adapters.tracer_otel import OtelTracer
from pkpipe.steps import register_code
from pkpipe.telemetry import REQUIRED_STEP_ATTRIBUTES, ContentPolicy
from tests.fakes import FakeModel, FakeTools
from tests.support.flows import make_ports, make_service, make_snapshot


@register_code("test.otel_boom")
def _boom(ctx, **kw):
    raise RuntimeError("private detail")


FLOW = {
    "id": "otel_flow",
    "start": "read",
    "nodes": [
        {"id": "read", "type": "tool", "bpmn_element": "Read new INBOX items", "config": {"tool": "t:read"}},
        {
            "id": "judge",
            "type": "llm",
            "bpmn_element": "Categorize each item",
            "for_each": "read.items",
            "inputs": ["item.text"],
            "config": {"system": "analyst", "output_schema": "verdict"},
        },
        {"id": "boom", "type": "code", "bpmn_element": "Code step", "config": {"function": "test.otel_boom"}},
        {"id": "handled", "type": "code", "config": {"function": "item.set", "args": {"ok": True}}},
    ],
    "edges": [
        {"from": "read", "to": "judge"},
        {"from": "judge", "to": "boom"},
        {"from": "boom", "to": "handled", "on": "error"},
    ],
}


def _run(capture_flows=()):
    tools = FakeTools(
        {"t:read": lambda: {"items": [{"id": "i1", "category": "tool", "text": "SECRET TEXT"}]}}
    )
    ports = make_ports(tools=tools, model=FakeModel([{"verdict": "fine"}]), tracer=OtelTracer())
    snap = make_snapshot(FLOW)
    outcome = make_service(snap, ports).start("otel_flow", trigger="schedule")
    assert outcome.ok, outcome.error
    return snap


def test_step_spans_carry_every_required_attribute(otel):
    snap = _run()
    spans = {s.name: s for s in otel.spans()}
    flow = spans["flow otel_flow"]
    assert flow.attributes["config.version"] == snap.version and flow.attributes["run.trigger"] == "schedule"
    judge = spans["step judge"]
    for key in REQUIRED_STEP_ATTRIBUTES:
        assert key in judge.attributes, key
    assert judge.attributes["item.id"] == "i1" and judge.attributes["item.category"] == "tool"
    assert judge.attributes["bpmn.element"] == "Categorize each item"
    assert judge.context.trace_id == flow.context.trace_id  # parallel item tasks stay in the run's trace
    llm = spans["llm default"]
    assert llm.attributes["flow.id"] == "otel_flow" and llm.attributes["item.id"] == "i1"
    assert llm.parent.span_id == judge.context.span_id
    assert spans["tool t:read"].attributes["config.version"] == snap.version


def test_llm_and_tool_spans_carry_the_step_attributes(otel):
    """Constitution X: every LLM call and tool call span has flow.id, step.id, bpmn.element, item.id,
    item.category and config.version, taken from the step that made the call."""
    snap = _run()
    spans = {s.name: s for s in otel.spans()}
    llm, tool = spans["llm default"], spans["tool t:read"]
    expected_llm = {
        "flow.id": "otel_flow",
        "step.id": "judge",
        "bpmn.element": "Categorize each item",
        "item.id": "i1",
        "item.category": "tool",
        "config.version": snap.version,
    }
    assert {k: llm.attributes.get(k) for k in expected_llm} == expected_llm
    assert tool.attributes["step.id"] == "read" and tool.attributes["bpmn.element"] == "Read new INBOX items"
    assert tool.attributes["flow.id"] == "otel_flow" and tool.attributes["config.version"] == snap.version


def test_handled_step_failure_is_marked_and_counted_without_private_text(otel):
    """Edge case: the failed code step is marked failed even though the error edge handled it."""
    _run()
    boom = next(s for s in otel.spans() if s.name == "step boom")
    assert boom.status.status_code is StatusCode.ERROR
    assert [e.attributes["exception.type"] for e in boom.events] == ["RuntimeError"]
    assert all("private detail" not in str(e.attributes) for e in boom.events)
    assert otel.metric_value("pkpipe.step_errors_total", **{"step.id": "boom"}) == 1
    assert (
        next(s for s in otel.spans() if s.name == "flow otel_flow").status.status_code is not StatusCode.ERROR
    )


def test_prompt_content_is_captured_only_for_opted_in_flows(otel_factory):
    """Spec 003 FR-004 through the engine: capture off by default, on for flows listed in telemetry.json."""
    off = otel_factory()
    _run()
    assert not any(k.startswith("pkpipe.content.") for s in off.spans() for k in s.attributes)
    on = otel_factory(ContentPolicy(frozenset({"otel_flow"})))
    _run()
    llm = next(s for s in on.spans() if s.name == "llm default")
    assert "SECRET TEXT" in llm.attributes["pkpipe.content.user"]
