"""Spec 001 T005: every port has an in-memory fake that honours the port contract."""

from __future__ import annotations

import inspect

import pytest

from pkpipe import ports
from pkpipe.engine.registry import config_version
from tests.fakes import (
    FakeClassifier,
    FakeClassifierReview,
    FakeClock,
    FakeContextStore,
    FakeImageStore,
    FakeItemEvents,
    FakeModel,
    FakeNotify,
    FakePreparer,
    FakeProposalAttempts,
    FakeSecurityLog,
    FakeSnapshots,
    FakeSystemProbe,
    FakeTools,
    InMemoryConfig,
    InMemoryRunStore,
    InMemoryState,
    RecordedSpan,
    RecordingTracer,
)

FAKES = {
    "ClockPort": FakeClock(),
    "TracerPort": RecordingTracer(),
    "SpanPort": RecordedSpan("step", "x"),
    "RunStorePort": InMemoryRunStore(),
    "ModelPort": FakeModel(),
    "ToolPort": FakeTools(),
    "NotifyPort": FakeNotify(),
    "StatePort": InMemoryState(),
    "ConfigPort": InMemoryConfig(),
    "ClassifierPort": FakeClassifier(),
    "ClassifierReviewPort": FakeClassifierReview(),
    "ItemEventPort": FakeItemEvents(),
    "SecurityLogPort": FakeSecurityLog(),
    "ContextStorePort": FakeContextStore(),
    "ImageStorePort": FakeImageStore(),
    "ProposalAttemptsPort": FakeProposalAttempts(),
    "ImagePreparePort": FakePreparer(),
    "SystemProbePort": FakeSystemProbe(),
    "SnapshotPort": FakeSnapshots(),
}


def test_every_port_has_a_fake():
    port_names = {n for n, o in vars(ports).items() if inspect.isclass(o) and n.endswith("Port")}
    assert port_names == set(FAKES), "add a fake in tests/fakes/ for every new port"


@pytest.mark.parametrize("name", sorted(FAKES))
def test_fake_satisfies_its_port(name):
    assert isinstance(FAKES[name], getattr(ports, name))


def test_fake_model_replays_script_and_records_calls():
    m = FakeModel([{"category": "tool"}, {"category": "misc"}])
    assert m.complete("sys", "u1", {"type": "object"}) == {"category": "tool"}
    assert m.complete("sys", "u2") == {"category": "misc"}
    assert [c["user"] for c in m.calls] == ["u1", "u2"] and m.calls[0]["json_schema"] == {"type": "object"}
    with pytest.raises(AssertionError, match="no scripted reply"):
        m.complete("sys", "u3")


def test_fake_model_callable_script():
    assert FakeModel(lambda s, u: {"echo": u}).complete("s", "hi") == {"echo": "hi"}


def test_fake_tools_list_respects_allow_list_and_call_records():
    t = FakeTools({"read_note": lambda path: f"body of {path}", "write_note": lambda path, body: None})
    assert t.list_tools(["read_note"]) == ["read_note"]
    assert t.list_tools([]) == []
    assert t.call("read_note", {"path": "a.md"}) == "body of a.md"
    assert t.calls == [("read_note", {"path": "a.md"})]
    with pytest.raises(KeyError):
        t.call("delete_everything", {})


def test_fake_notify_routes_collects_and_reports_failures():
    n = FakeNotify(routes={"ready": ["a", "b"]}, fail={"b"})
    deliveries = n.send("ready", {"message": "triage ready"})
    assert [(d.channel, d.ok) for d in deliveries] == [("a", True), ("b", False)]
    assert [s.message for s in n.to("a")] == ["triage ready"] and n.to("b") == []
    assert n.send("unrouted", {}) == []
    assert [d.channel for d in n.send("unrouted", {}, ["x"])] == ["x"]


def test_in_memory_state_roundtrip_isolates_copies():
    s = InMemoryState()
    assert s.get_item("1") is None
    item = {"id": "1", "state": "pending", "notes": []}
    s.save_item(item)
    item["state"] = "mutated-after-save"
    loaded = s.get_item("1")
    assert loaded["state"] == "pending"
    loaded["notes"].append("x")
    assert s.get_item("1")["notes"] == []


def test_in_memory_config_version_follows_effective_content():
    a = InMemoryConfig({"schedules": [{"flow": "t", "cron": "0 1 * * *"}]})
    b = InMemoryConfig(
        {"schedules": [{"flow": "t", "cron": "0 1 * * *"}]},
        {"schedules": [{"flow": "t", "cron": "0 2 * * *"}]},
    )
    assert a.version() == config_version({"schedules": [{"flow": "t", "cron": "0 1 * * *"}]})
    assert a.version() != b.version()
    assert len(b.load_layers()) == 2


def test_fake_model_accepts_profile_raises_scripted_errors_and_can_check_schema():
    from pkpipe.ports import ModelUnavailable, SchemaError

    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    m = FakeModel([ModelUnavailable("down"), {"n": 1}, {"n": "x"}], schema_check=True)
    with pytest.raises(ModelUnavailable):
        m.complete("s", "u", schema, profile="judge")
    assert m.complete("s", "u", schema) == {"n": 1}
    with pytest.raises(SchemaError):
        m.complete("s", "u", schema)
    assert [c["profile"] for c in m.calls] == ["judge", "default", "default"]
    assert FakeModel([SchemaError("x")]).calls == []


def test_fake_clock_moves_only_on_sleep():
    c = FakeClock(100.0)
    assert c.now() == 100.0
    c.sleep(60)
    c.advance(5)
    assert c.now() == 165.0 and c.sleeps == [60]


def test_in_memory_run_store_round_trip_and_immutable_fields():
    s = InMemoryRunStore()
    s.create_run({"run_id": "r1", "flow_id": "f", "status": "running"})
    s.update_run("r1", status="done")
    assert s.get_run("r1")["status"] == "done"
    assert [r["run_id"] for r in s.list_runs(status="done")] == ["r1"] and s.list_runs(status="failed") == []
    with pytest.raises(ValueError):
        s.update_run("r1", flow_id="other")
    s.save_snapshot("v1", {"a": 1}, {"x.md": "x"})
    s.save_snapshot("v1", {"a": 2}, {})  # first snapshot of a version wins (pinned)
    assert s.load_snapshot("v1") == ({"a": 1}, {"x.md": "x"}) and s.load_snapshot("v2") is None


def test_recording_tracer_marks_failed_spans_and_carries_flow_attributes():
    t = RecordingTracer()
    with t.flow("f", trigger="manual", config_version="v"), t.item("i1", "tool"):
        with pytest.raises(RuntimeError), t.step("s", "Task", item_id=None, item_category=None):
            raise RuntimeError("x")
    step = t.of("step")[0]
    assert step.failed == "RuntimeError"
    assert step.attributes["flow.id"] == "f" and step.attributes["item.id"] == "i1"


def test_fake_security_log_deny_lists_a_domain_once_and_counts():
    """Spec 009 edge case: the same domain flagged twice is one deny-list entry with a counter."""
    log = FakeSecurityLog("denied.example\n")
    assert log.deny_domain("evil.example", day="2026-11-01") == {
        "domain": "evil.example",
        "added": True,
        "count": 1,
    }
    assert log.deny_domain("evil.example", day="2026-11-02")["count"] == 2
    assert log.deny_domain("a.denied.example", day="2026-11-02") == {
        "domain": "a.denied.example",
        "added": False,
        "count": 1,
    }
    assert log.denied_domains() == {"denied.example", "evil.example"}
