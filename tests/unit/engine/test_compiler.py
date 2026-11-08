"""Spec 005 US1 (flows are data), FR-001/FR-003, edge cases: compiler validation, routing, errors, fan-out."""

from __future__ import annotations

import threading
import time

import pytest

from pkpipe.engine.compiler import FlowCompiler, FlowError, StepFailed, validate_config_flows
from pkpipe.engine.runs import RunStatus
from pkpipe.steps import STEPS, RunInfo, StepConfig, StepExecutor, StepResult, register_code
from tests.fakes import FakeModel, FakeTools, InMemoryRunStore, RecordingTracer
from tests.support.flows import make_ports, make_service, make_snapshot


@register_code("test.boom")
def _boom(ctx, **kw):
    raise RuntimeError("synthetic failure")


@register_code("test.echo")
def _echo(ctx, **kw):
    return {"kw": kw, "item": ctx.item}


def _compile(snapshot, flow_id, **kw):
    return FlowCompiler(
        snapshot, make_ports(), RunInfo("r", flow_id, "manual", snapshot.version), **kw
    ).compile(flow_id)


# -- US1 AS2: validation names the node ---------------------------------------------------------------------
def test_unknown_step_type_names_the_node():
    """US1-AS2: an unknown step type is a clear error that names flow and node."""
    snap = make_snapshot({"id": "f", "start": "x", "nodes": [{"id": "x", "type": "teleport"}]})
    with pytest.raises(FlowError) as exc:
        _compile(snap, "f")
    assert "flow 'f': node 'x' has unknown step type 'teleport'" in exc.value.errors[0]
    assert "llm" in exc.value.errors[0]  # lists the known types
    errors, _ = validate_config_flows(snap)
    assert any("node 'x'" in e and "teleport" in e for e in errors)


@pytest.mark.parametrize(
    "node, fragment",
    [
        ({"id": "x", "type": "code", "config": {"function": "nope"}}, "unknown code function 'nope'"),
        ({"id": "x", "type": "code", "config": {"function": "test.echo", "typo": 1}}, "config typo"),
        ({"id": "x", "type": "tool", "config": {"tool": "t:delete_all"}}, "not in the allow list of 't'"),
        ({"id": "x", "type": "tool", "config": {"tool": "web:get"}}, "unregistered MCP server 'web'"),
        (
            {"id": "x", "type": "llm", "config": {"system": "ghost"}},
            "system prompt 'prompts/system/ghost.md'",
        ),
        (
            {"id": "x", "type": "llm", "config": {"system": "analyst", "output_schema": "nope"}},
            "output schema",
        ),
        ({"id": "x", "type": "subflow", "config": {"flow": "missing"}}, "subflow 'missing' does not exist"),
        (
            {"id": "x", "type": "notify", "config": {"event": "e", "message": "m", "channels": ["ghost"]}},
            "ghost",
        ),
        (
            {"id": "x", "type": "code", "inputs": ["other.value"], "config": {"function": "test.echo"}},
            "'other.value'",
        ),
        ({"id": "x", "type": "guard", "config": {"input": "item.x", "patterns": ["("]}}, "invalid pattern"),
    ],
)
def test_node_config_and_references_are_validated(node, fragment):
    snap = make_snapshot({"id": "f", "start": "x", "nodes": [node]})
    errors, _ = validate_config_flows(snap)
    assert any(fragment in e for e in errors), errors


def test_tool_sessions_never_receive_raw_untrusted_input():
    """Constitution IV: an llm_tools node with raw item text as input does not compile."""
    node = {
        "id": "research",
        "type": "llm_tools",
        "inputs": ["item.body"],
        "config": {"system": "analyst", "tools": ["t:read"]},
    }
    errors, _ = validate_config_flows(make_snapshot({"id": "f", "start": "research", "nodes": [node]}))
    assert any("raw untrusted input ['item.body']" in e for e in errors)


def test_tool_sessions_never_receive_unvalidated_step_outputs():
    """Constitution IV: sessions with tools see only validated JSON. A tool result (for example a note body
    read from the INBOX) or a free-text session is refused; a code result or a schema-validated session is
    accepted."""
    nodes = [
        {"id": "read", "type": "tool", "config": {"tool": "t:read"}},
        {"id": "free", "type": "llm", "inputs": ["read.body"], "config": {"system": "analyst"}},
        {
            "id": "extract",
            "type": "llm",
            "inputs": ["read.body"],
            "config": {"system": "analyst", "output_schema": "verdict"},
        },
        {"id": "gate", "type": "code", "config": {"function": "item.set"}},
        {
            "id": "research",
            "type": "llm_tools",
            "inputs": ["read.body", "free.text", "extract.verdict", "gate.ok", "input.topic"],
            "config": {"system": "analyst", "tools": ["t:read"]},
        },
    ]
    edges = [
        {"from": "read", "to": "free"},
        {"from": "free", "to": "extract"},
        {"from": "extract", "to": "gate"},
        {"from": "gate", "to": "research"},
    ]
    flow = {"id": "f", "start": "read", "nodes": nodes, "edges": edges}
    errors, _ = validate_config_flows(make_snapshot(flow))
    [error] = [e for e in errors if "raw untrusted input" in e]
    assert "['free.text', 'read.body']" in error
    assert "extract.verdict" not in error and "gate.ok" not in error and "input.topic" not in error


def test_wait_cannot_be_a_fan_out_target_and_fan_out_takes_plain_edges():
    flow = {
        "id": "f",
        "start": "w",
        "nodes": [
            {
                "id": "w",
                "type": "wait",
                "for_each": "input.items",
                "config": {"tool": "t:status", "until": {"path": "result.s", "eq": 1}},
            },
            {"id": "c", "type": "code", "for_each": "input.items", "config": {"function": "test.echo"}},
        ],
        "edges": [{"from": "c", "to": "END", "on": "error"}],
    }
    errors, _ = validate_config_flows(make_snapshot(flow))
    assert any("cannot be a for_each target" in e for e in errors)
    assert any("takes only plain edges" in e for e in errors)


def test_subflow_cycle_is_rejected():
    a = {"id": "a", "start": "x", "nodes": [{"id": "x", "type": "subflow", "config": {"flow": "b"}}]}
    b = {"id": "b", "start": "y", "nodes": [{"id": "y", "type": "subflow", "config": {"flow": "a"}}]}
    errors, _ = validate_config_flows(make_snapshot(a, b))
    assert any("subflow cycle a -> b -> a" in e for e in errors)


def test_schedule_without_flow_is_a_warning():
    snap = make_snapshot(schedules=[{"cron": "0 1 * * *", "flow": "later"}])
    errors, warnings = validate_config_flows(snap)
    assert not errors and any("later" in w for w in warnings)


# -- US1 AS1: conditional edges route items -----------------------------------------------------------------
ROUTE_PARENT = {
    "id": "route",
    "start": "read",
    "nodes": [
        {"id": "read", "type": "tool", "config": {"tool": "t:read"}},
        {"id": "each", "type": "subflow", "for_each": "read.items", "config": {"flow": "route_item"}},
        {"id": "after", "type": "code", "config": {"function": "test.echo", "args": {"n": "${each}"}}},
    ],
    "edges": [{"from": "read", "to": "each"}, {"from": "each", "to": "after"}],
}
ROUTE_ITEM = {
    "id": "route_item",
    "nodes": [
        {
            "id": "learn",
            "type": "code",
            "bpmn_element": "Learning",
            "config": {"function": "item.set", "args": {"path": "learning"}},
        },
        {"id": "high", "type": "code", "config": {"function": "item.set", "args": {"path": "high"}}},
        {"id": "other", "type": "code", "config": {"function": "item.set", "args": {"path": "other"}}},
    ],
    "edges": [
        {"from": "START", "to": "learn", "when": {"path": "item.category", "eq": "learning-material"}},
        {"from": "START", "to": "high", "when": {"path": "item.score", "gte": 0.8}},
        {"from": "START", "to": "other", "default": True},
    ],
}
ITEMS = [
    {"id": "a", "category": "learning-material", "score": 0.9},
    {"id": "b", "category": "tool", "score": 0.95},
    {"id": "c", "category": "tool", "score": 0.1},
]


def test_conditional_edges_route_each_item():
    """US1-AS1: a flow JSON with conditional edges routes items by the condition (first match wins)."""
    tools = FakeTools({"t:read": lambda: {"items": ITEMS}})
    ports = make_ports(tools=tools)
    outcome = make_service(make_snapshot(ROUTE_PARENT, ROUTE_ITEM), ports).start("route")
    assert outcome.ok, outcome.error
    paths = {r["item"]["id"]: r["item"]["path"] for r in outcome.outputs["each"]}
    assert paths == {"a": "learning", "b": "high", "c": "other"}
    ran = {
        (s.name, s.attributes["item.id"])
        for s in ports.tracer.of("step")
        if s.name in ("learn", "high", "other")
    }
    assert ran == {("learn", "a"), ("high", "b"), ("other", "c")}
    assert len(ports.tracer.of("step", "after")) == 1  # the join after a fan-out runs once
    assert outcome.outputs["after"]["kw"]["n"][0]["status"] == "done"


def test_empty_fan_out_skips_to_successors():
    tools = FakeTools({"t:read": lambda: {"items": []}})
    outcome = make_service(make_snapshot(ROUTE_PARENT, ROUTE_ITEM), make_ports(tools=tools)).start("route")
    assert outcome.ok and outcome.outputs["each"] == [] and outcome.outputs["after"]["kw"]["n"] == []


# -- error edges and failures --------------------------------------------------------------------------------
def _fail_flow(error_edge: bool) -> dict:
    edges = [{"from": "boom", "to": "never"}]
    if error_edge:
        edges.append({"from": "boom", "to": "handle", "on": "error"})
    return {
        "id": "fail",
        "start": "boom",
        "nodes": [
            {"id": "boom", "type": "code", "bpmn_element": "Code step", "config": {"function": "test.boom"}},
            {"id": "never", "type": "code", "config": {"function": "test.echo"}},
            {
                "id": "handle",
                "type": "code",
                "config": {"function": "test.echo", "args": {"err": "${error.type}"}},
            },
        ],
        "edges": edges,
    }


def test_code_step_failure_follows_the_error_edge():
    """Edge case: a code step raises -> node marked failed, the item continues on the error edge."""
    ports = make_ports()
    outcome = make_service(make_snapshot(_fail_flow(True)), ports).start("fail")
    assert outcome.ok
    assert outcome.outputs["handle"]["kw"] == {"err": "RuntimeError"}
    assert "never" not in outcome.outputs
    assert ports.tracer.of("step", "boom")[0].failed == "RuntimeError"


def test_code_step_failure_without_error_edge_fails_the_run_with_span_error():
    """Edge case: no error edge -> the run fails; step and flow spans carry the error."""
    ports, store = make_ports(), InMemoryRunStore()
    outcome = make_service(make_snapshot(_fail_flow(False)), ports, store=store).start("fail")
    assert outcome.run.status is RunStatus.FAILED
    assert isinstance(outcome.error, StepFailed) and outcome.error.node_id == "boom"
    assert "RuntimeError: synthetic failure" in store.get_run(outcome.run.run_id)["error"]
    assert ports.tracer.of("step", "boom")[0].failed == "StepFailed"
    assert ports.tracer.of("flow")[0].failed == "StepFailed"
    assert not ports.tracer.of("step", "never")


def test_on_item_error_continue_keeps_the_other_items():
    flow = {
        "id": "items",
        "start": "work",
        "nodes": [
            {
                "id": "work",
                "type": "tool",
                "for_each": "input.items",
                "on_item_error": "continue",
                "config": {"tool": "t:read", "arguments": {"id": "${item.id}"}},
            },
            {"id": "done", "type": "code", "config": {"function": "test.echo"}},
        ],
        "edges": [{"from": "work", "to": "done"}],
    }

    def read(id):
        if id == "bad":
            raise ValueError("cannot read")
        return {"ok": id}

    svc = make_service(make_snapshot(flow), make_ports(tools=FakeTools({"t:read": read})))
    outcome = svc.start("items", inputs={"items": [{"id": "a"}, {"id": "bad"}, {"id": "c"}]})
    assert outcome.ok
    status = {r["item"]["id"]: r["status"] for r in outcome.outputs["work"]}
    assert status == {"a": "done", "bad": "failed", "c": "done"}
    failed = next(r for r in outcome.outputs["work"] if r["status"] == "failed")
    assert failed["error"]["type"] == "ValueError"


# -- FR-003: parallel fan-out with a limit -----------------------------------------------------------------
@pytest.mark.parametrize("limit", [2, 3])
def test_fan_out_runs_items_in_parallel_up_to_the_limit(limit):
    """FR-003: Send fan-out processes items in parallel, never more than max_parallel at once."""
    lock, state = threading.Lock(), {"active": 0, "peak": 0}

    def slow(id):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.15)
        with lock:
            state["active"] -= 1
        return {"id": id}

    flow = {
        "id": "par",
        "start": "work",
        "max_parallel": limit,
        "nodes": [
            {
                "id": "work",
                "type": "tool",
                "for_each": "input.items",
                "config": {"tool": "t:read", "arguments": {"id": "${item.id}"}},
            }
        ],
    }
    svc = make_service(make_snapshot(flow), make_ports(tools=FakeTools({"t:read": slow})))
    outcome = svc.start("par", inputs={"items": [{"id": str(i)} for i in range(6)]})
    assert outcome.ok and len(outcome.outputs["work"]) == 6
    assert state["peak"] == limit


# -- extensibility (T002) ------------------------------------------------------------------------------------
class _ShoutConfig(StepConfig):
    text: str


class _Shout(StepExecutor):
    type = "shout"
    Config = _ShoutConfig

    def run(self, node, config, ctx):
        return StepResult(output={"text": ctx.render(config.text).upper()})


def test_a_new_step_type_is_added_by_registration_only():
    """T002: later specs add step types by registering an executor; the engine is not edited."""
    flow = {
        "id": "s",
        "start": "x",
        "nodes": [{"id": "x", "type": "shout", "config": {"text": "hi ${input.who}"}}],
    }
    snap = make_snapshot(flow)
    with pytest.raises(FlowError):
        _compile(snap, "s")
    registry = STEPS.copy()
    registry.register(_Shout)
    outcome = make_service(snap, make_ports(), registry=registry).start("s", inputs={"who": "bob"})
    assert outcome.outputs["x"] == {"text": "HI BOB"}
    assert "shout" not in STEPS  # the global registry was not touched


def test_registering_a_second_executor_for_a_type_needs_replace():
    registry = STEPS.copy()

    class Other(StepExecutor):
        type = "llm"

    with pytest.raises(ValueError, match="already registered"):
        registry.register(Other)
    registry.register(Other, replace=True)
    assert type(registry.get("llm")) is Other


def test_step_plugins_load_from_entry_points(monkeypatch):
    """T002: an entry point may name an executor class or a register(registry) function; a broken one is
    skipped."""
    from pkpipe.steps import base

    class EP:
        def __init__(self, name, obj):
            self.name, self.obj = name, obj

        def load(self):
            if isinstance(self.obj, Exception):
                raise self.obj
            return self.obj

    def register(reg):
        reg.register(_Shout)

    class Whisper(_Shout):
        type = "whisper"

    eps = [EP("shout", register), EP("whisper", Whisper), EP("broken", ImportError("nope"))]
    monkeypatch.setattr(base, "entry_points", lambda group: eps if group == "pkpipe.steps" else [])
    registry = base.StepRegistry()
    loaded = base.load_plugins(registry=registry)
    assert loaded == ["shout", "whisper"] and registry.types() == ["shout", "whisper"]


# -- telemetry -----------------------------------------------------------------------------------------------
def test_every_step_llm_and_tool_call_is_traced_with_pkpipe_attributes():
    """Constitution X: flow, step, LLM and tool spans carry flow.id, step.id, bpmn.element, item.id,
    item.category and config.version."""
    flow = {
        "id": "traced",
        "start": "read",
        "nodes": [
            {"id": "read", "type": "tool", "bpmn_element": "Read", "config": {"tool": "t:read"}},
            {
                "id": "judge",
                "type": "llm",
                "bpmn_element": "Judge",
                "for_each": "read.items",
                "inputs": ["item.text"],
                "config": {"system": "analyst", "output_schema": "verdict"},
            },
        ],
        "edges": [{"from": "read", "to": "judge"}],
    }
    tracer = RecordingTracer()
    tools = FakeTools({"t:read": lambda: {"items": [{"id": "i1", "category": "tool", "text": "x"}]}})
    ports = make_ports(tools=tools, model=FakeModel([{"verdict": "ok"}]), tracer=tracer)
    snap = make_snapshot(flow)
    outcome = make_service(snap, ports).start("traced", trigger="schedule")
    assert outcome.ok
    judge = tracer.of("step", "judge")[0].attributes
    assert judge == {
        "flow.id": "traced",
        "step.id": "judge",
        "bpmn.element": "Judge",
        "item.id": "i1",
        "item.category": "tool",
        "config.version": snap.version,
        "run.trigger": "schedule",
        "pkpipe.step.type": "llm",
    }
    assert tracer.of("llm")[0].attributes["item.id"] == "i1"
    assert tracer.of("tool", "t:read")[0].attributes["flow.id"] == "traced"
    assert tracer.of("flow")[0].attributes["config.version"] == snap.version


def test_parallel_items_share_one_compiled_subflow():
    """Regression: parallel fan-out tasks asking for the same subflow at once must not see a false cycle."""
    snap = make_snapshot(ROUTE_PARENT, ROUTE_ITEM)
    compiler = FlowCompiler(snap, make_ports(), RunInfo("r", "route", "manual", snap.version))
    barrier, results, errors = threading.Barrier(8), [], []

    def worker():
        barrier.wait()
        try:
            results.append(compiler.subgraph("route_item"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len({id(g) for g in results}) == 1


# -- spec 030: every step of an item appends an event to the item log ----------------------------------------
def _event_flow(**extra):
    return {
        "id": "ev",
        "start": "work",
        "nodes": [
            {
                "id": "work",
                "type": "tool",
                "for_each": "input.items",
                "on_item_error": "continue",
                "config": {"tool": "t:read", "arguments": {"id": "${item.id}"}},
            }
        ],
        "edges": [],
        **extra,
    }


def _read(id):
    if id == "bad":
        raise ValueError("cannot read")
    return {"ok": id}


def test_every_step_of_an_item_is_logged_with_its_outcome(tmp_path):
    from pkpipe.adapters.app_store_sqlite import AppStore

    store = AppStore(tmp_path / "app.db")
    ports = make_ports(tools=FakeTools({"t:read": _read}), item_events=store)
    svc = make_service(make_snapshot(_event_flow()), ports)
    assert svc.start("ev", inputs={"items": [{"id": "a"}, {"id": "bad"}]}).ok
    good, bad = store.events_of("a"), store.events_of("bad")
    assert [(e["step"], e["kind"], e["outcome"]) for e in good] == [("work", "step", "done")]
    assert bad[0]["outcome"] == "failed" and "ValueError" in bad[0]["detail"]["error"]
    assert good[0]["flow_id"] == "ev" and good[0]["detail"]["type"] == "tool"


def test_a_private_flow_logs_content_free_events(tmp_path):
    from pkpipe.adapters.app_store_sqlite import AppStore

    store = AppStore(tmp_path / "app.db")
    ports = make_ports(tools=FakeTools({"t:read": _read}), item_events=store)
    svc = make_service(make_snapshot(_event_flow(data_classes=["journal"])), ports)
    svc.start("ev", inputs={"items": [{"id": "bad"}]})
    event = store.events_of("bad")[0]
    assert event["outcome"] == "failed" and event["detail"] == {}


def test_a_broken_event_log_does_not_stop_the_flow():
    class Broken:
        def record(self, *a, **k):
            raise RuntimeError("locked")

    ports = make_ports(tools=FakeTools({"t:read": _read}), item_events=Broken())
    assert make_service(make_snapshot(_event_flow()), ports).start("ev", inputs={"items": [{"id": "a"}]}).ok
