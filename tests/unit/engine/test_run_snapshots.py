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
    snaps = FakeSnapshots(fail=OSError("No space left on device"))
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


def test_no_configured_vault_logs_instead_of_blocking(caplog):
    snap = make_snapshot(flow("w", "t:write_note"), allow=WRITE)
    ports = make_ports(tools=FakeTools({"t:write_note": lambda: "ok"}))
    svc = make_service(snap, ports, snapshots_for=lambda s: None)
    with caplog.at_level("WARNING", logger="pkpipe.runs"):
        assert svc.start("w").ok
    assert "no vault is configured" in caplog.text
