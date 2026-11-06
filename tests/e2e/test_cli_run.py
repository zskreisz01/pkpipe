"""Spec 005 T011 / SC-001: ``pkpipe run <flow>`` end-to-end through the CLI on a synthetic temp vault.

The vault is served by the real obsidian-vault MCP server (subprocess, Streamable HTTP); the CLI uses the
real composition root (SQLite app DB, MCP tool adapter, OTel tracer). Only the model and the notifier are
swapped for fakes at the composition root.
"""

from __future__ import annotations

import json

import pytest

from pkpipe import app
from pkpipe.cli import main
from pkpipe.domain.triage import item_id_for
from pkpipe.obsidian import lists
from tests.fakes import FakeModel, FakeNotify
from tests.support.pack_runtime import PACK_ROUTES, TRIAGE_FILE, fake_model_reply, owner_decides
from tests.support.vault_mcp import free_port, http_server, write_policy

TOOL = item_id_for("0-INBOX/example-tool.md")
VIDEO = item_id_for("0-INBOX/example-video.md")


@pytest.fixture
def served_vault(tmp_path, synthetic_vault, write_json):
    with http_server(write_policy(tmp_path, synthetic_vault), free_port()) as url:
        overlay = tmp_path / "overlay"
        write_json(overlay, "mcp_servers.json", {"obsidian": {"url": url}})
        yield synthetic_vault, overlay, tmp_path / "app.db"


@pytest.fixture
def fakes(monkeypatch):
    # misc: a category without web content, so this CLI test needs no web-fetch server
    # (tests/e2e/test_category_sessions_e2e.py
    # runs the Tool plan through secure fetch)
    model = FakeModel(fake_model_reply({"CLI tool": "misc", "video": "learning-material"}))
    notify = FakeNotify(routes=PACK_ROUTES)
    monkeypatch.setattr(app, "make_model", lambda snapshot: model)
    monkeypatch.setattr(app, "make_notify", lambda snapshot: notify)
    monkeypatch.delenv("PKPIPE_OVERLAY_DIR", raising=False)
    return model, notify


def _run(capsys, *argv) -> tuple[int, dict | list]:
    code = main(list(argv))
    out = capsys.readouterr().out.strip().splitlines()
    return code, json.loads(out[-1]) if out else {}


def test_pkpipe_run_triage_then_process_released(served_vault, fakes, capsys):
    vault, overlay, db = served_vault
    model, notify = fakes
    common = ("--overlay", str(overlay), "--db", str(db), "--json")

    code, triage = _run(capsys, "run", "triage", *common)
    assert code == 0, triage
    assert triage["flow_id"] == "triage" and triage["status"] == "done" and triage["trigger"] == "manual"
    rows = {r["item_id"]: r for r in lists.read_triage(vault / TRIAGE_FILE)["rows"]}
    assert {k: v["ai_suggested_category"] for k, v in rows.items()} == {
        TOOL: "misc",
        VIDEO: "learning-material",
    }
    assert notify.sent[0].channel == "discord_main" and "2 new item(s)" in notify.sent[0].message
    assert len(model.calls) == 2

    owner_decides(vault, confirm=[TOOL])  # the video stays unreviewed
    code, processed = _run(capsys, "run", "process_released", *common, "--trigger", "ui")
    assert code == 0, processed
    [note_file] = (vault / "3-Resources/Misc").glob(f"*-{TOOL}.md")
    assert "pkpipe_item: " + TOOL in note_file.read_text(encoding="utf-8")
    assert (vault / "4-Archive/Inbox" / f"{TOOL}-example-tool.md").is_file()  # archived through the vault MCP
    rows = {r["item_id"]: r["state"] for r in lists.read_triage(vault / TRIAGE_FILE)["rows"]}
    assert rows == {TOOL: "processed", VIDEO: "pending"}
    assert notify.sent[-1].message == "Processed 1, quarantined 0, failed 0; 1 item(s) still pending."

    code, runs = _run(capsys, "runs", "--db", str(db), "--json")
    assert code == 0
    assert [(r["flow_id"], r["status"], r["trigger"]) for r in runs] == [
        ("triage", "done", "manual"),
        ("process_released", "done", "ui"),
    ]
    assert runs[0]["config_version"] == runs[1]["config_version"] == triage["config_version"]


def test_failed_run_exits_nonzero_and_is_recorded(served_vault, fakes, capsys, write_json):
    """A step failure ends the run as failed (exit 1) and the record says why."""
    vault, overlay, db = served_vault
    write_json(
        overlay, "mcp_servers.json", {"obsidian": {"url": "http://127.0.0.1:9/mcp"}}
    )  # nothing listens
    code, out = _run(capsys, "run", "triage", "--overlay", str(overlay), "--db", str(db), "--json")
    assert code == 1 and out["status"] == "failed" and "migrate" in out["error"]
    assert not (vault / TRIAGE_FILE).exists()


def test_resume_of_an_unknown_run_is_an_error(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("PKPIPE_OVERLAY_DIR", raising=False)
    assert main(["run", "--resume", "nope", "--db", str(tmp_path / "app.db")]) == 1
    assert "unknown run 'nope'" in capsys.readouterr().err
