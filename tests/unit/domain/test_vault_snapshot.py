"""Spec 019: the pure rules of snapshots and restore plans (no file system)."""

from __future__ import annotations

import pytest

from pkpipe.domain.vault_snapshot import (
    NOT_A_FILE,
    READ_ONLY_VAULT_TOOLS,
    WRITING_VAULT_TOOLS,
    FileEntry,
    PostEntry,
    SnapshotError,
    build_plan,
    check_run_id,
    diff_manifests,
    flow_writes_vault,
    reduce_folders,
    tool_names,
)
from pkpipe.obsidian.policy import TOOLS


def entry(path: str, sha: str, **kw) -> FileEntry:
    return FileEntry(path=path, size=len(sha), sha256=sha, mtime=1, **kw)


def pre_of(*entries: FileEntry) -> dict[str, FileEntry]:
    return {e.path: e for e in entries}


# -- classification of the vault tools -----------------------------------------------------------------------
def test_every_vault_tool_is_classified_as_writing_or_read_only():
    """Plan risk: a new vault tool must be classified, or a flow could write without a snapshot."""
    assert set(TOOLS) == WRITING_VAULT_TOOLS | READ_ONLY_VAULT_TOOLS
    assert not WRITING_VAULT_TOOLS & READ_ONLY_VAULT_TOOLS


def flows_of(**nodes):
    return {fid: {"id": fid, "nodes": ns} for fid, ns in nodes.items()}


def test_a_flow_writes_when_a_node_calls_a_writing_tool_by_any_server_name():
    flows = flows_of(
        w=[{"id": "a", "type": "tool", "config": {"tool": "vault:write_note", "arguments": {}}}],
        r=[{"id": "a", "type": "tool", "config": {"tool": "obsidian:read_note"}}],
    )
    assert flow_writes_vault(flows, "w") and not flow_writes_vault(flows, "r")


def test_tools_of_a_session_and_nested_calls_count():
    assert set(tool_names({"tools": ["x:read_note", "y:move_note"], "calls": [{"tool": "z:search"}]})) == {
        "read_note",
        "move_note",
        "search",
    }
    flows = flows_of(
        s=[{"id": "a", "type": "llm_tools", "config": {"tools": ["websearch:search", "o:append_note"]}}]
    )
    assert flow_writes_vault(flows, "s")


def test_subflows_count_and_a_runtime_chosen_subflow_counts_as_writing():
    flows = flows_of(
        parent=[{"id": "a", "type": "subflow", "config": {"flow": "child"}}],
        child=[{"id": "a", "type": "tool", "config": {"tool": "o:quarantine_note"}}],
        quiet=[{"id": "a", "type": "subflow", "config": {"flow": "reader"}}],
        reader=[{"id": "a", "type": "tool", "config": {"tool": "o:read_note"}}],
        dynamic=[{"id": "a", "type": "subflow", "config": {"flow": "${item.subflow}"}}],
        loop=[{"id": "a", "type": "subflow", "config": {"flow": "loop"}}],
    )
    assert flow_writes_vault(flows, "parent")
    assert not flow_writes_vault(flows, "quiet")
    assert flow_writes_vault(flows, "dynamic")
    assert not flow_writes_vault(flows, "loop")  # a cycle terminates


def test_text_that_only_looks_like_a_tool_is_not_a_call():
    flows = flows_of(p=[{"id": "a", "type": "llm", "config": {"prompt": "use o:write_note maybe"}}])
    assert not flow_writes_vault(flows, "p")


def test_run_ids_cannot_leave_the_snapshot_folder():
    assert check_run_id("5f0c2c3e-1111-2222-3333-444455556666") == "5f0c2c3e-1111-2222-3333-444455556666"
    for bad in ("", "../x", "a/b", ".hidden", "a..b", "x" * 200, "a b"):
        with pytest.raises(SnapshotError):
            check_run_id(bad)


def test_reduce_folders_drops_nested_folders():
    assert reduce_folders(["2-Areas/Journal", "2-Areas", "0-INBOX", "0-INBOX/_quarantine", "3/4"]) == [
        "0-INBOX",
        "2-Areas",
        "3/4",
    ]


# -- post manifest -------------------------------------------------------------------------------------------
def test_diff_names_modified_created_removed_and_moved():
    pre = pre_of(entry("a.md", "A1"), entry("b.md", "B1"), entry("inbox/n.md", "N1"), entry("keep.md", "K1"))
    now = pre_of(
        entry("a.md", "A2"), entry("keep.md", "K1"), entry("archive/n.md", "N1"), entry("new.md", "X1")
    )
    got = {e.path: (e.state, e.from_path) for e in diff_manifests(pre, now)}
    assert got == {
        "a.md": ("modified", None),
        "b.md": ("removed", None),
        "archive/n.md": ("moved_from", "inbox/n.md"),
        "new.md": ("created", None),
    }


def test_two_notes_with_the_same_bytes_are_paired_by_file_name():
    pre = pre_of(entry("in/x.md", "S"), entry("in/y.md", "S"))
    now = pre_of(entry("out/y.md", "S"), entry("out/x.md", "S"))
    got = {e.path: e.from_path for e in diff_manifests(pre, now)}
    assert got == {"out/x.md": "in/x.md", "out/y.md": "in/y.md"}


# -- restore plan --------------------------------------------------------------------------------------------
def plan(pre, post, state, **kw):
    return build_plan(pre, post, lambda p: state.get(p), **kw)


def verbs(p):
    return [(s.verb, s.path or s.from_path, s.reason) for s in p.steps]


def test_plan_restores_removes_and_moves_back_what_the_run_did():
    pre = pre_of(entry("m.md", "M1"), entry("gone.md", "G1"), entry("in/n.md", "N1"))
    post = [
        PostEntry(path="m.md", state="modified", sha256="M2"),
        PostEntry(path="gone.md", state="removed"),
        PostEntry(path="new.md", state="created", sha256="C1"),
        PostEntry(path="arc/n.md", state="moved_from", sha256="N1", **{"from": "in/n.md"}),
    ]
    state = {"m.md": "M2", "new.md": "C1", "arc/n.md": "N1"}
    p = plan(pre, post, state)
    assert verbs(p) == [
        ("move_back", "arc/n.md", None),
        ("restore", "gone.md", None),
        ("restore", "m.md", None),
        ("remove", "new.md", None),
    ]
    assert p.steps[0].to == "in/n.md"


def test_a_file_the_owner_changed_after_the_run_is_a_conflict_not_a_step():
    pre = pre_of(entry("m.md", "M1"), entry("new.md", "x"))
    post = [
        PostEntry(path="m.md", state="modified", sha256="M2"),
        PostEntry(path="new.md", state="created", sha256="C1"),
        PostEntry(path="d.md", state="removed"),
    ]
    pre["d.md"] = entry("d.md", "D1")
    p = plan(pre, post, {"m.md": "OWNER", "new.md": "OWNER2", "d.md": "OWNER3"})
    assert [(s.verb, s.reason) for s in p.steps] == [("skip", "changed_since_run")] * 3
    assert not p.actionable


def test_force_saves_the_current_bytes_first_and_still_acts():
    pre = pre_of(entry("m.md", "M1"))
    post = [
        PostEntry(path="m.md", state="modified", sha256="M2"),
        PostEntry(path="c.md", state="created", sha256="C"),
    ]
    p = plan(pre, post, {"m.md": "OWNER", "c.md": "OWNER"}, force=True)
    restore = next(s for s in p.steps if s.verb == "restore")
    assert restore.save_current and restore.reason == "forced: changed_since_run"
    assert next(s for s in p.steps if s.verb == "remove").path == "c.md"


def test_a_restore_planned_twice_has_nothing_to_do_the_second_time():
    pre = pre_of(entry("m.md", "M1"), entry("in/n.md", "N1"), entry("g.md", "G1"))
    post = [
        PostEntry(path="m.md", state="modified", sha256="M2"),
        PostEntry(path="new.md", state="created", sha256="C1"),
        PostEntry(path="arc/n.md", state="moved_from", sha256="N1", **{"from": "in/n.md"}),
        PostEntry(path="g.md", state="removed"),
    ]
    after_restore = {"m.md": "M1", "in/n.md": "N1", "g.md": "G1"}  # new.md and arc/n.md are gone
    assert plan(pre, post, after_restore).steps == []


def test_a_destination_that_exists_again_blocks_the_move_back_unless_forced():
    pre = pre_of(entry("in/n.md", "N1"))
    post = [PostEntry(path="arc/n.md", state="moved_from", sha256="N1", **{"from": "in/n.md"})]
    state = {"arc/n.md": "N1", "in/n.md": "OTHER"}
    assert verbs(plan(pre, post, state)) == [("skip", "arc/n.md", "destination_exists")]
    forced = plan(pre, post, state, force=True)
    assert [(s.verb, s.save_current) for s in forced.steps] == [("restore", True), ("remove", False)]


def test_uncopied_and_conflict_files_are_never_restored_or_overwritten():
    pre = pre_of(
        entry("big.md", "B1", copied=False), entry("n (conflict).md", "C1", conflict=True, copied=False)
    )
    post = [
        PostEntry(path="big.md", state="modified", sha256="B2"),
        PostEntry(path="n (conflict).md", state="removed"),
    ]
    p = plan(pre, post, {"big.md": "B2"}, force=True)
    assert [(s.verb, s.reason) for s in p.steps] == [("skip", "not_copied"), ("skip", "sync_conflict_copy")]


def test_a_folder_or_symlink_in_the_way_reads_as_changed():
    pre = pre_of(entry("m.md", "M1"))
    post = [PostEntry(path="m.md", state="modified", sha256="M2")]
    assert verbs(plan(pre, post, {"m.md": NOT_A_FILE})) == [("skip", "m.md", "changed_since_run")]


def test_a_note_deleted_by_the_owner_after_the_run_is_reported():
    pre = pre_of(entry("m.md", "M1"))
    post = [PostEntry(path="m.md", state="modified", sha256="M2")]
    assert verbs(plan(pre, post, {})) == [("skip", "m.md", "deleted_since_run")]
