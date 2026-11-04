"""Spec 019: the file adapter on a temp vault (real files, real app DB index)."""

from __future__ import annotations

import hashlib
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pkpipe.adapters.app_store_sqlite import AppStore
from pkpipe.adapters.snapshot_files import FileSnapshots
from pkpipe.domain.vault_snapshot import SnapshotError, UnknownSnapshot
from pkpipe.obsidian.policy import VaultPolicy


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    files = {
        "0-INBOX/Inbox-Triage.md": "triage v1\n",
        "0-INBOX/a.md": "note a\n",
        "0-INBOX/_quarantine/q.md": "quarantined\n",
        "1-Projects/p.md": "project\n",
        "3-Resources/Tools/t.md": "tool\n",
        "2-Areas/Journal/2026-11-01.md": "PRIVATE JOURNAL\n",
        "0-INBOX/attachments/pic.png": "not markdown",
        "0-INBOX/n (conflict).md": "conflict copy\n",
        "elsewhere/outside.md": "outside every scope\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


@pytest.fixture
def snaps(tmp_path, vault):
    store = AppStore(tmp_path / "app.db")
    return FileSnapshots(
        tmp_path / "data" / "snapshots", vault, index=store, policy=VaultPolicy(vault_dir=vault)
    )


def test_take_copies_the_scope_and_nothing_else(snaps, vault):
    info = snaps.take("run-1", "triage")
    base = snaps.directory / "run-1"
    copied = tree(base / "files")
    assert set(copied) == {
        "0-INBOX/Inbox-Triage.md",
        "0-INBOX/a.md",
        "0-INBOX/_quarantine/q.md",
        "1-Projects/p.md",
        "3-Resources/Tools/t.md",
    }
    assert all(copied[k] == (vault / k).read_bytes() for k in copied)
    manifest = snaps.show("run-1")
    assert manifest["not_copied"] == ["0-INBOX/n (conflict).md"]  # listed, never copied
    assert info.files == 6 and info.bytes == sum(len(v) for v in copied.values())
    assert "2-Areas/Journal/2026-11-01.md" not in tree(base)  # the journal is never copied
    assert (base / "manifest.json").is_file()


def test_the_snapshot_folder_is_private_and_outside_the_vault(snaps, vault):
    snaps.take("run-1", "triage")
    mode = stat.S_IMODE(os.stat(snaps.directory).st_mode)
    assert mode == 0o700 and stat.S_IMODE(os.stat(snaps.directory / "run-1").st_mode) == 0o700
    assert vault.resolve() not in snaps.directory.resolve().parents


def test_symlinks_and_hidden_files_are_never_followed_or_copied(snaps, vault, tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("outside the vault")
    (vault / "1-Projects" / "link.md").symlink_to(secret)
    (vault / "1-Projects" / "linked").symlink_to(tmp_path / "data", target_is_directory=True)
    (vault / "1-Projects" / ".hidden.md").write_text("hidden")
    (vault / "1-Projects" / ".obsidian").mkdir()
    (vault / "1-Projects" / ".obsidian" / "x.md").write_text("hidden folder")
    snaps.take("run-1", "triage")
    assert "1-Projects/link.md" not in tree(snaps.directory / "run-1")
    names = {f for f in tree(snaps.directory / "run-1" / "files")}
    assert names == {
        "0-INBOX/Inbox-Triage.md",
        "0-INBOX/a.md",
        "0-INBOX/_quarantine/q.md",
        "1-Projects/p.md",
        "3-Resources/Tools/t.md",
    }


def test_a_failing_snapshot_raises_and_leaves_no_folder(snaps, vault, monkeypatch):
    def boom(*a, **k):
        raise OSError(28, "No space left on device", str(vault / "0-INBOX" / "secret-name.md"))

    monkeypatch.setattr(Path, "write_bytes", boom)
    with pytest.raises(SnapshotError) as err:
        snaps.take("run-1", "triage")
    assert "No space left on device" in str(err.value) and "secret-name" not in str(err.value)
    assert not (snaps.directory / "run-1").exists()
    assert not [p for p in snaps.directory.iterdir()]


def test_taking_twice_for_one_run_is_refused(snaps):
    snaps.take("run-1", "triage")
    with pytest.raises(SnapshotError, match="exists already"):
        snaps.take("run-1", "triage")


def test_take_without_a_policy_raises(tmp_path, vault):
    snaps = FileSnapshots(tmp_path / "s", vault, index=AppStore(tmp_path / "a.db"))
    with pytest.raises(SnapshotError, match="no vault policy"):
        snaps.take("r", "f")


def test_the_manifest_records_the_hash_of_the_copied_bytes(snaps, vault):
    snaps.take("run-1", "triage")
    base = snaps.directory / "run-1"
    import json

    manifest = json.loads((base / "manifest.json").read_text())
    for f in manifest["files"]:
        if f["copied"]:
            assert hashlib.sha256((base / "files" / f["path"]).read_bytes()).hexdigest() == f["sha256"]
    assert manifest["version"] == 1 and manifest["policy_digest"] and manifest["scope"]


def run_changes(vault: Path) -> None:
    """What a run does: modify a managed file, create two notes, move one."""
    (vault / "0-INBOX/Inbox-Triage.md").write_text("triage v2\n")
    (vault / "3-Resources/Tools/new1.md").write_text("new 1\n")
    (vault / "3-Resources/Tools/new2.md").write_text("new 2\n")
    (vault / "4-Archive/Inbox").mkdir(parents=True)
    (vault / "0-INBOX/a.md").rename(vault / "4-Archive/Inbox/a.md")
    (vault / "1-Projects/p.md").unlink()


def test_restore_returns_the_vault_to_the_state_before_the_run(snaps, vault):
    before = tree(vault)
    snaps.take("run-1", "triage")
    run_changes(vault)
    snaps.finish("run-1")

    plan = snaps.plan_restore("run-1")
    assert plan.counts() == {"restore": 2, "remove": 2, "move_back": 1, "skip": 0}
    assert tree(vault) != before  # the dry run changed nothing

    applied = snaps.apply_restore("run-1")
    assert applied.applied and all(s.outcome == "done" for s in applied.steps)
    assert tree(vault) == before  # SC-001: same bytes, same file set
    trash = tree(snaps.directory / "run-1" / "trash")
    assert trash == {"3-Resources/Tools/new1.md": b"new 1\n", "3-Resources/Tools/new2.md": b"new 2\n"}


def test_dry_run_is_the_default_and_changes_nothing(snaps, vault):
    snaps.take("run-1", "triage")
    run_changes(vault)
    snaps.finish("run-1")
    state = tree(vault)
    plan = snaps.plan_restore("run-1")
    assert not plan.applied and tree(vault) == state
    assert snaps.list()[0].status == "kept"


def test_an_owner_edit_after_the_run_is_skipped_without_force(snaps, vault):
    snaps.take("run-1", "triage")
    run_changes(vault)
    snaps.finish("run-1")
    (vault / "0-INBOX/Inbox-Triage.md").write_text("triage v2 plus the owner's tick\n")
    plan = snaps.apply_restore("run-1")
    skipped = [s for s in plan.steps if s.verb == "skip"]
    assert [(s.path, s.reason) for s in skipped] == [("0-INBOX/Inbox-Triage.md", "changed_since_run")]
    assert (vault / "0-INBOX/Inbox-Triage.md").read_text() == "triage v2 plus the owner's tick\n"  # SC-004
    assert (vault / "1-Projects/p.md").read_text() == "project\n"  # the rest was restored


def test_force_overwrites_after_saving_the_current_bytes_in_the_trash(snaps, vault):
    snaps.take("run-1", "triage")
    run_changes(vault)
    snaps.finish("run-1")
    (vault / "0-INBOX/Inbox-Triage.md").write_text("owner edit\n")
    plan = snaps.apply_restore("run-1", force=True)
    assert all(s.outcome == "done" for s in plan.steps)
    assert (vault / "0-INBOX/Inbox-Triage.md").read_text() == "triage v1\n"
    assert tree(snaps.directory / "run-1" / "trash")["0-INBOX/Inbox-Triage.md"] == b"owner edit\n"


def test_a_note_the_owner_never_touched_but_the_run_did_not_either_is_left_alone(snaps, vault):
    snaps.take("run-1", "triage")
    (vault / "3-Resources/Tools/new1.md").write_text("new\n")
    snaps.finish("run-1")
    (vault / "1-Projects/p.md").write_text("owner edited this after the run\n")
    snaps.apply_restore("run-1")
    assert (vault / "1-Projects/p.md").read_text() == "owner edited this after the run\n"
    assert not (vault / "3-Resources/Tools/new1.md").exists()


def test_applying_a_restore_twice_changes_nothing_the_second_time(snaps, vault):
    snaps.take("run-1", "triage")
    run_changes(vault)
    snaps.finish("run-1")
    snaps.apply_restore("run-1")
    state, n = tree(vault), len(snaps.list())
    again = snaps.apply_restore("run-1")
    assert again.steps == [] and again.safety_snapshot is None
    assert tree(vault) == state and len(snaps.list()) == n  # SC-003: no change, no extra snapshot


def test_a_restore_takes_a_snapshot_first_and_is_undoable(snaps, vault):
    snaps.take("run-1", "triage")
    run_changes(vault)
    snaps.finish("run-1")
    after_run = tree(vault)
    plan = snaps.apply_restore("run-1")
    assert plan.safety_snapshot and plan.safety_snapshot.startswith("restore-run-1-")
    assert tree(vault) != after_run
    snaps.apply_restore(plan.safety_snapshot)  # undo the restore
    assert tree(vault) == after_run


def test_a_restore_marks_the_run_reverted_in_the_index(snaps, vault, tmp_path):
    snaps.take("run-1", "triage")
    run_changes(vault)
    snaps.finish("run-1")
    snaps.apply_restore("run-1")
    row = AppStore(tmp_path / "app.db").snapshot_get("run-1")
    assert row["status"] == "restored" and row["reverted"] == 1 and row["restored_at"]
    info = {i.run_id: i for i in snaps.list()}["run-1"]
    assert info.status == "restored" and info.reverted and info.finished


def test_an_unfinished_run_has_no_post_manifest_and_is_refused(snaps, vault):
    snaps.take("run-1", "triage")
    with pytest.raises(SnapshotError, match="has not finished"):
        snaps.plan_restore("run-1")


def test_finish_without_a_snapshot_is_a_noop_and_unknown_runs_raise(snaps):
    assert snaps.finish("never-snapshotted") is None
    with pytest.raises(UnknownSnapshot):
        snaps.plan_restore("never-snapshotted")
    with pytest.raises(SnapshotError):
        snaps.plan_restore("../etc")


def test_finishing_again_after_a_resume_replaces_the_record(snaps, vault):
    snaps.take("run-1", "triage")
    (vault / "1-Projects/x.md").write_text("first half\n")
    snaps.finish("run-1")
    (vault / "1-Projects/y.md").write_text("second half\n")
    snaps.finish("run-1")
    states = {c["path"] for c in snaps.show("run-1")["changes"]}
    assert states == {"1-Projects/x.md", "1-Projects/y.md"}


def test_a_symlinked_folder_in_the_restore_path_is_refused(snaps, vault, tmp_path):
    snaps.take("run-1", "triage")
    (vault / "1-Projects/p.md").unlink()
    snaps.finish("run-1")
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault / "1-Projects").rename(vault / "1-Projects-moved")
    (vault / "1-Projects").symlink_to(outside, target_is_directory=True)
    plan = snaps.apply_restore("run-1")
    assert not list(outside.iterdir())  # nothing written through the link
    assert all(s.outcome != "done" for s in plan.steps)


def test_follows_a_real_layout_not_the_pack_names(tmp_path):
    from tests.unit.obsidian.test_snapshot_scope import real_policy

    root = tmp_path / "v"
    for rel in ("1-Inbox/INBOX.md", "4-Resources/r.md", "DailyJournals/2026/d.md", "0-INBOX/old.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")
    snaps = FileSnapshots(tmp_path / "s", root, index=AppStore(tmp_path / "a.db"), policy=real_policy(root))
    snaps.take("r", "f")
    assert set(tree(tmp_path / "s" / "r" / "files")) == {"1-Inbox/INBOX.md", "4-Resources/r.md"}


def test_big_files_are_manifest_only(tmp_path, vault):
    (vault / "1-Projects/big.md").write_text("x" * 100)
    snaps = FileSnapshots(
        tmp_path / "s",
        vault,
        index=AppStore(tmp_path / "a.db"),
        policy=VaultPolicy(vault_dir=vault),
        max_file_bytes=50,
    )
    snaps.take("r", "f")
    assert "1-Projects/big.md" in snaps.show("r")["not_copied"]
    (vault / "1-Projects/big.md").write_text("changed by the run")
    snaps.finish("r")
    plan = snaps.plan_restore("r")
    assert [(s.verb, s.reason) for s in plan.steps] == [("skip", "not_copied")]


# -- retention -------------------------------------------------------------------------------------
def test_prune_removes_old_snapshots_but_keeps_the_newest_of_each_flow(tmp_path, vault):
    clock = {"now": datetime(2026, 10, 1, tzinfo=UTC)}
    store = AppStore(tmp_path / "app.db")
    snaps = FileSnapshots(
        tmp_path / "s",
        vault,
        index=store,
        policy=VaultPolicy(vault_dir=vault),
        retention_days=14,
        now=lambda: clock["now"],
    )
    for run, flow, day in (("old-t", "triage", 1), ("old-t2", "triage", 2), ("old-p", "process", 3)):
        clock["now"] = datetime(2026, 10, day, tzinfo=UTC)
        snaps.take(run, flow)
    clock["now"] = datetime(2026, 11, 4, tzinfo=UTC)
    result = snaps.prune()
    assert result["pruned"] == ["old-t"]  # old-t2 and old-p are the newest of their flows
    assert {i.run_id: i.status for i in snaps.list()} == {
        "old-t2": "kept",
        "old-p": "kept",
        "old-t": "pruned",
    }
    assert not (snaps.directory / "old-t").exists()
    assert store.snapshot_get("old-t")["status"] == "pruned"


def test_prune_keeps_the_total_under_max_bytes_oldest_first(tmp_path, vault):
    clock = {"now": datetime(2026, 11, 1, tzinfo=UTC)}
    snaps = FileSnapshots(
        tmp_path / "s",
        vault,
        index=AppStore(tmp_path / "a.db"),
        policy=VaultPolicy(vault_dir=vault),
        now=lambda: clock["now"],
        max_bytes=10**9,
    )
    for i in range(3):
        clock["now"] += timedelta(hours=1)
        snaps.take(f"r{i}", "triage")
    snaps.max_bytes = snaps.list()[0].bytes * 2
    assert snaps.prune()["pruned"] == ["r0"]
    assert [i.run_id for i in snaps.list() if i.status == "kept"] == ["r2", "r1"]


def test_summary_reports_directory_free_space_and_newest(snaps):
    assert snaps.summary()["newest"] is None
    snaps.take("run-1", "triage")
    s = snaps.summary()
    assert s["snapshots"] == 1 and s["newest"]["run_id"] == "run-1" and s["free_bytes"] > 0


def test_spans_carry_run_id_and_counts_never_paths_or_text(snaps, vault, otel):
    """FR-007 (constitution X): paths can name private notes."""
    snaps.take("run-1", "triage")
    run_changes(vault)
    snaps.finish("run-1")
    snaps.apply_restore("run-1")
    spans = otel.spans("tool snapshot.")
    assert {s.name for s in spans} >= {"tool snapshot.take", "tool snapshot.finish", "tool snapshot.restore"}
    take = next(s for s in spans if s.name == "tool snapshot.take")
    assert (
        take.attributes["pkpipe.snapshot.run_id"] == "run-1" and take.attributes["pkpipe.snapshot.files"] == 6
    )
    restore = next(s for s in spans if s.name == "tool snapshot.restore")
    assert restore.attributes["pkpipe.snapshot.restored_files"] == 5
    blob = " ".join(f"{s.name} {dict(s.attributes)}" for s in spans)
    for private in ("Inbox-Triage", "a.md", "new1", "Tools", "0-INBOX", "note a", "project"):
        assert private not in blob, private


# -- overlapping runs (spec 019 edge case: a restore of a run that is not the newest touching a file) ---
def test_a_restore_of_an_overlapping_run_reports_the_other_runs_writes_as_conflicts(snaps, vault):
    """Two runs in flight: each post manifest is a full rescan, so run A's record holds run B's note.
    Restoring A after both ended would silently revert B's work; the plan names the overlap instead."""
    snaps.take("run-a", "flow-a")
    snaps.take("run-b", "flow-b")  # starts before A ended
    (vault / "3-Resources/Tools/from-a.md").write_text("written by A\n")
    (vault / "3-Resources/Tools/from-b.md").write_text("written by B\n")
    snaps.finish("run-a")
    snaps.finish("run-b")
    after = tree(vault)

    plan = snaps.apply_restore("run-a")
    assert tree(vault) == after  # nothing was reverted, B's note included
    assert {s.path: (s.verb, (s.reason or "").split(":")[0]) for s in plan.steps} == {
        "3-Resources/Tools/from-a.md": ("skip", "overlapping_run"),
        "3-Resources/Tools/from-b.md": ("skip", "overlapping_run"),
    }
    assert plan.safety_snapshot is None  # no actionable step: no safety snapshot, no change

    forced = snaps.apply_restore("run-a", force=True)  # the owner decides, with the safety snapshot
    assert all(s.outcome == "done" for s in forced.steps) and forced.safety_snapshot
    assert not (vault / "3-Resources/Tools/from-a.md").exists()


def test_runs_one_after_the_other_do_not_count_as_overlapping(snaps, vault):
    before = tree(vault)
    snaps.take("run-a", "triage")
    (vault / "3-Resources/Tools/from-a.md").write_text("A\n")
    snaps.finish("run-a")
    snaps.take("run-b", "triage")
    (vault / "3-Resources/Tools/from-b.md").write_text("B\n")
    snaps.finish("run-b")
    plan = snaps.plan_restore("run-a")
    assert [(s.verb, s.path) for s in plan.steps] == [("remove", "3-Resources/Tools/from-a.md")]
    snaps.apply_restore("run-b")
    snaps.apply_restore("run-a")
    assert tree(vault) == before


# -- prune must not take the snapshot a restore is using -------------------------------------------
def test_a_restore_is_not_pruned_away_by_its_own_safety_snapshot(tmp_path, vault):
    """The safety snapshot adds bytes: with a tight ``max_bytes`` the snapshot being restored (not the
    newest of its flow) was the oldest and prune removed it mid-restore."""
    snaps = FileSnapshots(
        tmp_path / "s", vault, index=AppStore(tmp_path / "a.db"), policy=VaultPolicy(vault_dir=vault)
    )
    before = tree(vault)
    snaps.take("old", "triage")
    run_changes(vault)
    snaps.finish("old")
    snaps.take("newer", "triage")
    snaps.finish("newer")
    snaps.max_bytes = 1  # everything but the newest of each flow is over budget

    plan = snaps.apply_restore("old")
    assert all(s.outcome == "done" for s in plan.steps), [s.outcome for s in plan.steps]
    assert tree(vault) == before
    assert (snaps.directory / "old" / "manifest.json").is_file()
    assert not (snaps.directory / "old" / ".restoring").exists()  # the marker ends with the restore
    assert (snaps.directory / "old" / "trash" / "3-Resources/Tools/new1.md").is_file()
    assert {p.name for p in snaps.directory.iterdir()} == {p.name for p in snaps._run_dirs()}  # no orphans


def test_prune_leaves_a_snapshot_with_a_fresh_restore_marker(tmp_path, vault):
    clock = {"now": datetime(2026, 10, 1, tzinfo=UTC)}
    snaps = FileSnapshots(
        tmp_path / "s",
        vault,
        index=AppStore(tmp_path / "a.db"),
        policy=VaultPolicy(vault_dir=vault),
        now=lambda: clock["now"],
    )
    snaps.take("old", "triage")
    clock["now"] = datetime(2026, 10, 2, tzinfo=UTC)
    snaps.take("new", "triage")
    clock["now"] = datetime(2026, 11, 4, tzinfo=UTC)
    marker = snaps.directory / "old" / ".restoring"
    marker.write_text("x")
    os.utime(marker, (clock["now"].timestamp(), clock["now"].timestamp()))
    assert snaps.prune()["pruned"] == []
    os.utime(marker, (clock["now"].timestamp() - 7200, clock["now"].timestamp() - 7200))  # a crashed restore
    assert snaps.prune()["pruned"] == ["old"]


def test_trashing_into_a_vanished_snapshot_folder_creates_nothing(snaps, vault):
    snaps.take("run-1", "triage")
    run_changes(vault)
    snaps.finish("run-1")
    base = snaps.directory / "run-1"
    import shutil

    shutil.rmtree(base)
    with pytest.raises(SnapshotError, match="vanished"):
        snaps._to_trash(base, "3-Resources/Tools/new1.md", keep_original=False)
    assert not base.exists() and (vault / "3-Resources/Tools/new1.md").is_file()


# -- the check and the write of a restore step -----------------------------------------------------
def test_an_edit_between_the_check_and_the_write_is_never_overwritten_unseen(snaps, vault):
    """Obsidian (or a sync client) saves a file after the step's precondition check: restore, remove and
    move_back each look again right before they act and skip instead of losing the edit."""
    snaps.take("run-1", "triage")
    run_changes(vault)
    snaps.finish("run-1")
    edited = {
        "0-INBOX/Inbox-Triage.md",
        "3-Resources/Tools/new1.md",
        "3-Resources/Tools/new2.md",
        "4-Archive/Inbox/a.md",
        "1-Projects/p.md",
    }
    armed = {"on": False, "done": False}
    original_sha, original_step = snaps._current_sha, snaps._apply_step

    def racing_sha(rel):
        result = original_sha(rel)
        if armed["on"] and not armed["done"] and rel in edited and (vault / rel).exists():
            armed["done"] = True  # the edit lands right after the first look at this file
            (vault / rel).write_text(f"edit of {rel}")
        return result

    def step(s, *a, **k):
        armed.update(on=True, done=False)
        try:
            return original_step(s, *a, **k)
        finally:
            armed["on"] = False

    snaps._current_sha, snaps._apply_step = racing_sha, step  # type: ignore[method-assign]
    plan = snaps.apply_restore("run-1")
    actionable = [s for s in plan.steps if s.verb != "skip"]
    assert actionable and {s.outcome for s in plan.steps} <= {"skipped:changed_during_restore", "done"}
    for rel in ("0-INBOX/Inbox-Triage.md", "3-Resources/Tools/new1.md", "3-Resources/Tools/new2.md"):
        assert (vault / rel).read_text() == f"edit of {rel}"  # every edit survived
    assert (vault / "4-Archive/Inbox/a.md").read_text() == "edit of 4-Archive/Inbox/a.md"
    assert not (vault / "0-INBOX/a.md").exists()  # the move_back did not leave a half done copy
    trash = (
        tree(snaps.directory / "run-1" / "trash") if (snaps.directory / "run-1" / "trash").exists() else {}
    )
    assert not any(k.startswith("3-Resources/Tools/new") for k in trash)  # no stale copy of a kept file


# -- a note over the size limit is announced, not silently unprotected -----------------------------
def test_files_that_are_not_copied_are_reported_in_the_span_and_the_log(tmp_path, vault, otel, caplog):
    (vault / "1-Projects/big.md").write_text("x" * 100)
    snaps = FileSnapshots(
        tmp_path / "s",
        vault,
        index=AppStore(tmp_path / "a.db"),
        policy=VaultPolicy(vault_dir=vault),
        max_file_bytes=50,
    )
    with caplog.at_level("WARNING", logger="pkpipe.snapshots"):
        snaps.take("r", "f")
    span = next(s for s in otel.spans("tool snapshot.") if s.name == "tool snapshot.take")
    assert span.attributes["pkpipe.snapshot.not_copied"] == 1  # the sync-conflict copy is not counted
    assert "1 file(s) over max_file_bytes" in caplog.text and "big.md" not in caplog.text


# -- the scope must match the layout the writer uses -----------------------------------------------
def test_take_refuses_a_policy_with_another_layout_than_the_config(tmp_path, vault):
    from pkpipe.domain.layout import VaultLayout

    snaps = FileSnapshots(
        tmp_path / "s",
        vault,
        index=AppStore(tmp_path / "a.db"),
        policy=VaultPolicy(vault_dir=vault),
        expected_layout=VaultLayout(inbox_dir="1-Inbox", triage_file="1-Inbox/Inbox-Triage.md"),
    )
    with pytest.raises(SnapshotError, match="another layout"):
        snaps.take("r", "f")
    assert not (snaps.directory / "r").exists()


def test_take_accepts_the_policy_of_the_same_layout(tmp_path, vault):
    from pkpipe.domain.layout import VaultLayout

    snaps = FileSnapshots(
        tmp_path / "s",
        vault,
        index=AppStore(tmp_path / "a.db"),
        policy=VaultPolicy(vault_dir=vault),
        expected_layout=VaultLayout(),
    )
    assert snaps.take("r", "f").files


# -- SC-002 ----------------------------------------------------------------------------------------
def _thousand_notes(tmp_path: Path) -> Path:
    root = tmp_path / "big"
    for i in range(1000):
        p = root / "3-Resources" / f"f{i % 10}" / f"n{i}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"note {i}\n" * 20)
    return root


def test_a_snapshot_reads_every_note_once_and_calls_no_model(tmp_path, monkeypatch):
    """SC-002, the part that does not depend on the machine: 1 000 notes cost 1 000 reads (linear, no
    rescans) and the adapter has no model. The time bound is the opt-in test below."""
    root = _thousand_notes(tmp_path)
    snaps = FileSnapshots(
        tmp_path / "s", root, index=AppStore(tmp_path / "a.db"), policy=VaultPolicy(vault_dir=root)
    )
    reads = []
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: reads.append(self) or original(self))
    info = snaps.take("r", "f")
    assert info.files == 1000 and len(reads) == 1000
    import pkpipe.adapters.snapshot_files as module

    assert not any("model" in name.lower() for name in vars(module))


@pytest.mark.skipif(
    not os.environ.get("PKPIPE_PERF_TESTS"), reason="timing: PKPIPE_PERF_TESTS=1 (machine dependent)"
)
def test_a_thousand_notes_snapshot_in_under_two_seconds(tmp_path):
    """SC-002 wall clock; opt-in because a loaded CI runner makes it flaky."""
    import time

    root = _thousand_notes(tmp_path)
    snaps = FileSnapshots(
        tmp_path / "s", root, index=AppStore(tmp_path / "a.db"), policy=VaultPolicy(vault_dir=root)
    )
    t0 = time.perf_counter()
    info = snaps.take("r", "f")
    assert info.files == 1000 and time.perf_counter() - t0 < 2.0
