"""Spec 019 US2 / US3 / FR-004: ``pkpipe restore`` and ``pkpipe snapshot`` on a synthetic vault and a real
app DB. The snapshot is taken by the same factory ``pkpipe run`` uses (``app.make_snapshots``)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pkpipe import app
from pkpipe.adapters.app_store_sqlite import AppStore
from pkpipe.adapters.state_sqlite import SqliteRunStore
from pkpipe.cli import main
from pkpipe.engine.registry import load_effective


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def world(tmp_path, synthetic_vault, pack_dir, monkeypatch):
    monkeypatch.delenv("PKPIPE_VAULT_POLICY", raising=False)
    monkeypatch.setenv("PKPIPE_DATA_DIR", str(tmp_path / "data"))
    db = tmp_path / "app.db"
    common = ["--pack", str(pack_dir), "--db", str(db), "--vault", str(synthetic_vault)]
    snap = load_effective(pack_dir, None).snapshot()
    port = app.make_snapshots(snap, db, synthetic_vault)
    assert port is not None
    before = tree(synthetic_vault)
    port.take("5f0c2c3e-0000-4000-8000-000000000001", "triage")
    (synthetic_vault / "0-INBOX/new-note.md").write_text("created by the run\n")
    (synthetic_vault / "0-INBOX/example-tool.md").write_text("modified by the run\n")
    port.finish("5f0c2c3e-0000-4000-8000-000000000001")
    return synthetic_vault, common, before, db, port


def run_cli(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


RUN = "5f0c2c3e-0000-4000-8000-000000000001"


def test_restore_is_a_dry_run_unless_apply(world, capsys):
    vault, common, before, db, _ = world
    changed = tree(vault)
    code, out, _ = run_cli(capsys, "restore", RUN, *common)
    assert code == 0 and "dry run, nothing changed" in out
    assert "restore   0-INBOX/example-tool.md" in out and "remove    0-INBOX/new-note.md" in out
    assert tree(vault) == changed


def test_restore_apply_returns_the_vault_and_audits_without_paths(world, capsys):
    vault, common, before, db, _ = world
    code, out, _ = run_cli(capsys, "restore", RUN, "--apply", *common)
    assert code == 0 and "applied" in out and "not restored: the app DB" in out
    assert tree(vault) == before
    audit = AppStore(db).audit_log()[0]
    assert audit["action"] == "restore_run" and audit["actor"] == "cli" and audit["target"] == RUN
    assert audit["detail"]["restore"] == 1 and audit["detail"]["remove"] == 1
    assert "new-note" not in json.dumps(audit)  # paths never go into the audit log


def test_an_owner_edit_makes_the_restore_exit_2_and_force_overwrites(world, capsys):
    vault, common, before, db, _ = world
    (vault / "0-INBOX/example-tool.md").write_text("owner edit\n")
    code, out, _ = run_cli(capsys, "restore", RUN, "--apply", *common)
    assert code == 2 and "skipped:changed_since_run" in out and "--force" in out
    assert (vault / "0-INBOX/example-tool.md").read_text() == "owner edit\n"
    assert not (vault / "0-INBOX/new-note.md").exists()
    code, out, _ = run_cli(capsys, "restore", RUN, "--apply", "--force", *common)
    assert code == 0
    assert tree(vault) == before


def test_a_run_prefix_names_the_snapshot(world, capsys):
    vault, common, *_ = world
    code, out, _ = run_cli(capsys, "restore", RUN[:8], "--json", *common)
    assert code == 0 and json.loads(out)["run_id"] == RUN


def test_an_active_run_is_never_restored(world, capsys):
    vault, common, before, db, _ = world
    SqliteRunStore(db).create_run(
        {
            "run_id": RUN,
            "flow_id": "triage",
            "config_version": "v",
            "trigger": "manual",
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
            "ended_at": None,
            "error": None,
        }
    )
    code, _, err = run_cli(capsys, "restore", RUN, "--apply", *common)
    assert code == 1 and "restore it after it ended" in err
    assert (vault / "0-INBOX/new-note.md").exists()


def test_an_unknown_run_and_a_missing_vault_are_errors(world, capsys, monkeypatch, pack_dir, tmp_path):
    vault, common, *_ = world
    code, _, err = run_cli(capsys, "restore", "nope", *common)
    assert code == 1 and "no snapshot of run 'nope'" in err
    monkeypatch.delenv("PKPIPE_VAULT_DIR", raising=False)
    code, _, err = run_cli(capsys, "restore", RUN, "--pack", str(pack_dir), "--db", str(tmp_path / "x.db"))
    assert code == 1 and "no vault" in err


def test_snapshot_list_show_and_prune(world, capsys):
    vault, common, before, db, port = world
    code, out, _ = run_cli(capsys, "snapshot", "list", *common)
    assert code == 0 and RUN in out and "triage" in out and "kept" in out and "5 files" not in out
    code, out, _ = run_cli(capsys, "snapshot", "list", "--json", *common)
    (row,) = json.loads(out)
    assert row["run_id"] == RUN and row["status"] == "kept" and row["finished"] is True and row["files"] > 0
    code, out, _ = run_cli(capsys, "snapshot", "show", RUN[:8], *common)
    assert code == 0 and "created" in out and "0-INBOX/new-note.md" in out and "modified" in out
    code, out, _ = run_cli(capsys, "snapshot", "prune", *common)
    assert code == 0 and "pruned 0" in out  # the newest snapshot of a flow is always kept
    run_cli(capsys, "restore", RUN, "--apply", *common)
    code, out, _ = run_cli(capsys, "snapshot", "list", *common)
    assert "restored reverted" in out


def test_the_cli_has_restore_and_snapshot_commands(capsys):
    with pytest.raises(SystemExit):
        main(["restore", "--help"])
    assert "--apply" in capsys.readouterr().out


def test_a_too_short_run_prefix_says_how_long_it_has_to_be(world, capsys):
    vault, common, *_ = world
    code, out, err = run_cli(capsys, "restore", "5f0c", *common)
    assert code != 0 and "at least 6 characters" in (out + err)


def test_the_plan_names_the_snapshot_scope_and_that_other_folders_are_not_restored(world, capsys):
    vault, common, *_ = world
    code, out, _ = run_cli(capsys, "restore", RUN, *common)
    assert code == 0 and "snapshot scope: " in out and "0-INBOX" in out
    assert "outside these folders is not restored" in out


def test_a_policy_with_another_layout_than_layout_json_stops_the_snapshot(
    tmp_path, synthetic_vault, pack_dir, monkeypatch
):
    """The vault server runs with the policy file; a scope from another layout would miss what it writes."""
    from pkpipe.domain.vault_snapshot import SnapshotError

    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "layout": {
                    "archive_dir": "4-Archive/Elsewhere",
                    "inbox_archive_pattern": "4-Archive/Elsewhere/{yyyy-MM-dd}.md",
                }
            }
        )
    )
    snapshot = load_effective(pack_dir, None).snapshot()  # (loading with the variable set already fails)
    monkeypatch.setenv("PKPIPE_VAULT_POLICY", str(policy))
    monkeypatch.setenv("PKPIPE_DATA_DIR", str(tmp_path / "data"))
    port = app.make_snapshots(snapshot, tmp_path / "app.db", synthetic_vault)
    assert port is not None
    with pytest.raises(SnapshotError, match="another layout"):
        port.take("run-x", "triage")
