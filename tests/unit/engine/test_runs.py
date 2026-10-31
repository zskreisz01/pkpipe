"""Spec 005 US2 / FR-004 / T006-T007: run records, config version pinning, resume from checkpoints."""

from __future__ import annotations

import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from pkpipe.engine.runs import RunStatus, Trigger
from pkpipe.steps import register_code
from tests.fakes import FakeModel, FakeTools, InMemoryRunStore, RecordingTracer
from tests.support.flows import ASSETS, make_ports, make_service, make_snapshot


class Crash(BaseException):
    """Simulates the process dying mid-run: not an Exception, so nothing in the engine handles it."""


CRASH = {"armed": False}


@register_code("test.crash_once")
def _crash_once(ctx, **kw):
    if CRASH["armed"]:
        CRASH["armed"] = False
        raise Crash()
    return {"passed": True}


def _flow(tag: str) -> dict:
    return {
        "id": "pinned",
        "start": "first",
        "nodes": [
            {"id": "first", "type": "tool", "config": {"tool": "t:read", "arguments": {"tag": tag}}},
            {"id": "gate", "type": "code", "config": {"function": "test.crash_once"}},
            {"id": "ask", "type": "llm", "inputs": ["first.tag"], "config": {"system": "analyst"}},
            {"id": "last", "type": "tool", "config": {"tool": "t:done", "arguments": {"tag": tag}}},
        ],
        "edges": [
            {"from": "first", "to": "gate"},
            {"from": "gate", "to": "ask"},
            {"from": "ask", "to": "last"},
        ],
    }


def test_run_record_has_id_flow_version_trigger_status_and_times():
    """FR-004: run id, flow id, config version, trigger and status are stored."""
    store = InMemoryRunStore()
    snap = make_snapshot(_flow("v1"))
    tools = FakeTools({"t:read": lambda tag: {"tag": tag}, "t:done": lambda tag: tag})
    ports = make_ports(tools=tools, model=FakeModel([{"text": "ok"}]))
    outcome = make_service(snap, ports, store=store).start("pinned", trigger="schedule")
    rec = store.get_run(outcome.run.run_id)
    assert uuid.UUID(rec["run_id"]) and rec["flow_id"] == "pinned"
    assert rec["config_version"] == snap.version and rec["trigger"] == "schedule" and rec["status"] == "done"
    assert rec["started_at"] <= rec["ended_at"] and rec["error"] is None
    assert store.load_snapshot(snap.version) == (snap.data, snap.assets)


def test_unknown_flow_is_rejected_before_anything_is_recorded():
    store = InMemoryRunStore()
    with pytest.raises(KeyError, match="unknown flow 'ghost'"):
        make_service(make_snapshot(_flow("v1")), make_ports(), store=store).start("ghost")
    assert store.runs == {}


def test_trigger_must_be_known():
    with pytest.raises(ValueError):
        make_service(make_snapshot(_flow("v1")), make_ports()).start("pinned", trigger="cron-ish")


def test_resume_uses_the_pinned_config_and_skips_finished_steps():
    """US2-AS2: a config publish during a run does not change it; the resumed run uses its pinned version
    (flow arguments and prompt assets), and steps finished before the crash are not executed again."""
    v1 = make_snapshot(_flow("v1"))
    v2 = make_snapshot(_flow("v2"), assets={**ASSETS, "prompts/system/analyst.md": "NEW PROMPT"})
    assert v1.version != v2.version
    current = {"cfg": v1}
    store, saver = InMemoryRunStore(), InMemorySaver()
    tools = FakeTools({"t:read": lambda tag: {"tag": tag}, "t:done": lambda tag: tag})
    model = FakeModel(lambda system, user: {"text": "answer"})
    tracer = RecordingTracer()
    ports = make_ports(tools=tools, model=model, tracer=tracer)
    svc = make_service(lambda: current["cfg"], ports, store=store, checkpointer=saver)

    CRASH["armed"] = True
    with pytest.raises(Crash):
        svc.start("pinned", run_id="run-1")
    assert store.get_run("run-1")["status"] == "running"  # left behind by the "dead" process

    current["cfg"] = v2  # publish while the run is interrupted
    outcomes = svc.resume_incomplete()
    assert [o.run.run_id for o in outcomes] == ["run-1"] and outcomes[0].ok
    assert [c for c in tools.calls] == [("t:read", {"tag": "v1"}), ("t:done", {"tag": "v1"})]
    assert model.calls[0]["system"].startswith("You are an analyst.")
    assert all(f.attributes["config.version"] == v1.version for f in tracer.of("flow"))
    assert store.get_run("run-1")["config_version"] == v1.version

    # a new run picks up the published version
    fresh = svc.start("pinned")
    assert fresh.ok and fresh.run.config_version == v2.version
    assert model.calls[-1]["system"].startswith("NEW PROMPT") and tools.calls[-1] == ("t:done", {"tag": "v2"})


def test_only_active_runs_resume():
    store = InMemoryRunStore()
    tools = FakeTools({"t:read": lambda tag: {"tag": tag}, "t:done": lambda tag: tag})
    svc = make_service(
        make_snapshot(_flow("v1")), make_ports(tools=tools, model=FakeModel([{"text": "a"}])), store=store
    )
    run = svc.start("pinned").run
    assert run.status is RunStatus.DONE and run.trigger is Trigger.MANUAL
    with pytest.raises(ValueError, match="only running or waiting"):
        svc.resume(run.run_id)
    with pytest.raises(KeyError):
        svc.resume("nope")
    assert svc.resume_incomplete() == []


def test_resume_fails_clearly_when_the_snapshot_is_gone():
    store = InMemoryRunStore()
    store.create_run(
        {
            "run_id": "r",
            "flow_id": "pinned",
            "config_version": "abc",
            "trigger": "manual",
            "status": "waiting",
            "started_at": "2026-01-01T00:00:00+00:00",
        }
    )
    with pytest.raises(LookupError, match="snapshot"):
        make_service(make_snapshot(_flow("v1")), make_ports(), store=store).resume("r")


def _orphan(store: InMemoryRunStore, run_id: str, version: str, status: str = "running") -> None:
    store.create_run(
        {
            "run_id": run_id,
            "flow_id": "pinned",
            "config_version": version,
            "trigger": "schedule",
            "status": status,
            "started_at": "2026-01-01T00:00:00+00:00",
        }
    )


def test_run_recorded_without_a_checkpoint_fails_clearly_and_runs_nothing():
    """A process that died between recording the run and its first checkpoint: there is nothing to resume
    (the inputs were never checkpointed), so the run is failed with a clear reason and no step executes."""
    snap = make_snapshot(_flow("v1"))
    store = InMemoryRunStore()
    store.save_snapshot(snap.version, snap.data, snap.assets)
    _orphan(store, "orphan", snap.version)
    tools = FakeTools({"t:read": lambda tag: {"tag": tag}, "t:done": lambda tag: tag})
    svc = make_service(snap, make_ports(tools=tools), store=store)
    [outcome] = svc.resume_incomplete()
    assert outcome.run.status is RunStatus.FAILED and "no checkpoint" in outcome.run.error
    assert store.get_run("orphan")["status"] == "failed" and tools.calls == []


def test_resume_incomplete_keeps_going_when_one_snapshot_is_missing():
    """One broken run (its pinned snapshot is gone) is recorded as failed; the other runs still resume."""
    snap = make_snapshot(_flow("v1"))
    store, saver = InMemoryRunStore(), InMemorySaver()
    tools = FakeTools({"t:read": lambda tag: {"tag": tag}, "t:done": lambda tag: tag})
    svc = make_service(
        snap,
        make_ports(tools=tools, model=FakeModel(lambda s, u: {"text": "a"})),
        store=store,
        checkpointer=saver,
    )
    _orphan(store, "lost", "0" * 64, status="waiting")
    CRASH["armed"] = True
    with pytest.raises(Crash):
        svc.start("pinned", run_id="ok")
    outcomes = {o.run.run_id: o for o in svc.resume_incomplete()}
    assert outcomes["ok"].ok
    assert outcomes["lost"].run.status is RunStatus.FAILED and "snapshot" in outcomes["lost"].run.error
    assert store.get_run("lost")["status"] == "failed"
