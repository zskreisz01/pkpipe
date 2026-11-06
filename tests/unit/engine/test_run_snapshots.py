"""Spec 019 FR-001 / FR-003, US1: the run service snapshots before a writing flow and records the end."""

from __future__ import annotations

from tests.fakes import FakeModel, FakeSnapshots, FakeTools, InMemoryRunStore
from tests.support.flows import make_ports, make_service, make_snapshot

WRITE = ["read", "status", "done", "fail", "write_note", "read_note"]


def flow(fid: str, tool: str) -> dict:
    return {
        "id": fid,
        "start": "a",
        "nodes": [{"id": "a", "type": "tool", "config": {"tool": tool, "arguments": {}}}],
        "edges": [],
    }


def service(snap, snapshots, *, tools=None, store=None):
    ports = make_ports(tools=tools or FakeTools({"t:write_note": lambda: "ok", "t:read_note": lambda: "r"}))
    return make_service(snap, ports, store=store, snapshots_for=lambda s: snapshots)


def test_a_writing_flow_has_a_snapshot_with_the_run_id_before_its_first_node():
    order: list[str] = []
    snaps = FakeSnapshots()
    real_take = snaps.take
    snaps.take = lambda run_id, flow_id: (order.append("take"), real_take(run_id, flow_id))[1]
    tools = FakeTools({"t:write_note": lambda: order.append("write") or "ok"})
    svc = service(make_snapshot(flow("w", "t:write_note"), allow=WRITE), snaps, tools=tools)
    out = svc.start("w")
    assert out.ok and order == ["take", "write"]
    assert snaps.calls == [("take", out.run.run_id), ("finish", out.run.run_id)]


def test_a_read_only_flow_takes_no_snapshot():
    snaps = FakeSnapshots()
    svc = service(make_snapshot(flow("r", "t:read_note"), allow=WRITE), snaps)
    assert svc.start("r").ok
    assert snaps.calls == []


def test_a_failing_snapshot_stops_the_run_before_it_starts_and_names_the_cause():
    snaps = FakeSnapshots(fail=OSError(28, "No space left on device"))
    wrote: list[str] = []
    tools = FakeTools({"t:write_note": lambda: wrote.append("x") or "ok"})
    store = InMemoryRunStore()
    svc = service(make_snapshot(flow("w", "t:write_note"), allow=WRITE), snaps, tools=tools, store=store)
    out = svc.start("w")
    assert not out.ok and wrote == []  # no node ran
    rec = store.get_run(out.run.run_id)
    assert rec["status"] == "failed" and "No space left on device" in rec["error"]
    assert "did not start" in rec["error"]


def test_a_failed_run_still_records_its_end_so_the_partial_writes_can_be_undone():
    snaps = FakeSnapshots()

    def broken():
        raise RuntimeError("model went away")

    svc = service(
        make_snapshot(flow("w", "t:write_note"), allow=WRITE),
        snaps,
        tools=FakeTools({"t:write_note": broken}),
    )
    out = svc.start("w")
    assert not out.ok
    assert [c[0] for c in snaps.calls] == ["take", "finish"]


def test_without_a_snapshot_port_the_run_service_behaves_as_before():
    svc = make_service(
        make_snapshot(flow("w", "t:write_note"), allow=WRITE),
        make_ports(tools=FakeTools({"t:write_note": lambda: "ok"}), model=FakeModel()),
    )
    assert svc.start("w").ok


def test_a_writing_subflow_makes_the_parent_flow_snapshot():
    parent = {
        "id": "parent",
        "start": "s",
        "nodes": [{"id": "s", "type": "subflow", "config": {"flow": "child"}}],
        "edges": [],
    }
    snaps = FakeSnapshots()
    svc = service(make_snapshot(parent, flow("child", "t:write_note"), allow=WRITE), snaps)
    assert svc.start("parent").ok
    assert [c[0] for c in snaps.calls] == ["take", "finish"]


def test_no_configured_vault_blocks_a_writing_flow(caplog):
    """The vault server writes but this process has no vault for the snapshot: writing without a way back is
    not allowed (FR-001), so the run does not start."""
    wrote = []
    snap = make_snapshot(flow("w", "t:write_note"), allow=WRITE)
    ports = make_ports(tools=FakeTools({"t:write_note": lambda: wrote.append(1)}))
    svc = make_service(snap, ports, snapshots_for=lambda s: None)
    out = svc.start("w")
    assert not out.ok and not wrote
    assert "no vault is configured for snapshots" in out.run.error and "PKPIPE_VAULT_DIR" in out.run.error


def test_no_configured_vault_does_not_block_a_read_only_flow():
    snap = make_snapshot(flow("r", "t:read_note"), allow=["read_note"])
    ports = make_ports(tools=FakeTools({"t:read_note": lambda: "ok"}))
    assert make_service(snap, ports, snapshots_for=lambda s: None).start("r").ok


# -- fail closed: no port, and a resume whose snapshot is gone -----------------------------------------
import pytest  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from pkpipe.domain.vault_snapshot import UnknownSnapshot  # noqa: E402
from pkpipe.engine.runs import RunService  # noqa: E402
from pkpipe.steps import register_code  # noqa: E402


class _Died(BaseException):
    """The process dies mid-run: not an Exception, so nothing in the engine handles it."""


_DIE = {"armed": False}


@register_code("test.snapshot_die_once")
def _die_once(ctx, **kw):
    if _DIE["armed"]:
        _DIE["armed"] = False
        raise _Died()
    return {"passed": True}


def _two_step_writer() -> dict:
    return {
        "id": "w",
        "start": "a",
        "nodes": [
            {"id": "a", "type": "tool", "config": {"tool": "t:read_note", "arguments": {}}},
            {"id": "gate", "type": "code", "config": {"function": "test.snapshot_die_once"}},
            {"id": "b", "type": "tool", "config": {"tool": "t:write_note", "arguments": {}}},
        ],
        "edges": [{"from": "a", "to": "gate"}, {"from": "gate", "to": "b"}],
    }


class _Port(FakeSnapshots):
    """``show`` knows only the snapshots that were taken (a pruned one is unknown)."""

    def show(self, run_id):
        if run_id not in self.taken:
            raise UnknownSnapshot(f"no snapshot of run '{run_id}'")
        return {}


def _crashed_writer(snaps):
    wrote: list[str] = []
    tools = FakeTools({"t:read_note": lambda: "r", "t:write_note": lambda: wrote.append("x") or "ok"})
    store = InMemoryRunStore()
    svc = make_service(
        make_snapshot(_two_step_writer(), allow=WRITE),
        make_ports(tools=tools),
        store=store,
        checkpointer=InMemorySaver(),
        snapshots_for=lambda s: snaps,
    )
    _DIE["armed"] = True
    with pytest.raises(_Died):
        svc.start("w", run_id="run-1")
    assert store.get_run("run-1")["status"] == "running" and wrote == []
    return svc, store, wrote


def test_a_resumed_writing_run_keeps_going_while_its_snapshot_exists():
    snaps = _Port()
    svc, store, wrote = _crashed_writer(snaps)
    [outcome] = svc.resume_incomplete()
    assert outcome.ok and wrote == ["x"]


def test_a_resumed_writing_run_whose_snapshot_was_pruned_does_not_write():
    """Waiting or crashed runs can outlive their snapshot (retention, size cap): writing with no way back is
    not allowed on a resume either, so the run fails before its next node."""
    snaps = _Port()
    svc, store, wrote = _crashed_writer(snaps)
    snaps.taken.clear()  # pruned meanwhile
    [outcome] = svc.resume_incomplete()
    assert not outcome.ok and wrote == []
    rec = store.get_run("run-1")
    assert rec["status"] == "failed" and "without its snapshot" in rec["error"]


def test_a_resumed_writing_run_with_no_port_does_not_write():
    snaps = _Port()
    svc, store, wrote = _crashed_writer(snaps)
    svc.snapshots_for = lambda s: None  # restarted without a vault configured
    [outcome] = svc.resume_incomplete()
    assert not outcome.ok and wrote == []
    assert "no vault is configured for snapshots" in store.get_run("run-1")["error"]


def _bare_service(**kw):
    wrote: list[str] = []
    ports = make_ports(tools=FakeTools({"t:write_note": lambda: wrote.append("x") or "ok"}))
    svc = RunService(
        store=InMemoryRunStore(),
        checkpointer=InMemorySaver(),
        config=lambda: make_snapshot(flow("w", "t:write_note"), allow=WRITE),
        ports_for=lambda s: ports,
        **kw,
    )
    return svc, wrote


def test_a_run_service_built_without_a_snapshot_port_refuses_writing_flows():
    """FR-001 holds for every RunService, not only the one app.py builds."""
    svc, wrote = _bare_service()
    out = svc.start("w")
    assert not out.ok and wrote == []
    assert "no snapshot port" in out.run.error and "did not start" in out.run.error


def test_the_opt_out_of_snapshots_is_explicit():
    svc, wrote = _bare_service(unsnapshotted_writes=True)
    assert svc.start("w").ok and wrote == ["x"]
