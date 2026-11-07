"""Spec 006 US1 / T001-T005 end-to-end: ``pkpipe run`` triage and process_released deliver notifications
through the real notify step, router and Discord / Teams adapters to a local capturing HTTP server.
Only the model is faked; the vault is the real MCP server on a synthetic vault."""

from __future__ import annotations

import json

import pytest

from pkpipe import app
from pkpipe.cli import main
from pkpipe.domain.triage import item_id_for
from tests.fakes import FakeModel
from tests.support.pack_runtime import fake_model_reply, owner_decides
from tests.support.vault_mcp import free_port, http_server, write_policy
from tests.support.webhook_server import webhook_server

TOOL = item_id_for("0-INBOX/example-tool.md")


@pytest.fixture
def world(tmp_path, synthetic_vault, write_json, monkeypatch):
    model = FakeModel(fake_model_reply({"CLI tool": "tool", "video": "learning-material"}))
    monkeypatch.setattr(app, "make_model", lambda snapshot: model)
    monkeypatch.delenv("PKPIPE_OVERLAY_DIR", raising=False)
    with webhook_server() as hooks, http_server(write_policy(tmp_path, synthetic_vault), free_port()) as url:
        overlay = tmp_path / "overlay"
        write_json(overlay, "mcp_servers.json", {"obsidian": {"url": url}})
        write_json(
            overlay,
            "channels.json",
            {
                "channels": {
                    "discord_main": {"type": "discord_webhook", "url": "env:DISCORD_WEBHOOK_URL"},
                    "teams_main": {"type": "teams_webhook", "url": "env:TEAMS_WEBHOOK_URL"},
                },
                "routes": {
                    "triage_ready": ["discord_main", "teams_main"],
                    "daily_summary": ["discord_main", "teams_main"],
                },
            },
        )
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", hooks.base_url + "/discord/SECRET")
        monkeypatch.setenv("TEAMS_WEBHOOK_URL", hooks.base_url + "/teams/SECRET")
        yield synthetic_vault, hooks, ("--overlay", str(overlay), "--db", str(tmp_path / "app.db"), "--json")


def _run(capsys, *argv):
    code = main(list(argv))
    lines = capsys.readouterr().out.strip().splitlines()
    return code, json.loads(lines[-1])


def test_triage_and_summary_notifications_reach_discord_and_teams(world, capsys):
    vault, hooks, common = world
    code, triage = _run(capsys, "run", "triage", *common)
    assert code == 0 and triage["status"] == "done"
    assert "**Triage run**" in hooks.on("/discord/SECRET")[0].body["content"]
    assert "**New suggestions (2):**" in hooks.on("/discord/SECRET")[0].body["content"]
    card = hooks.on("/teams/SECRET")[0].body["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard" and "**New suggestions (2):**" in card["body"][1]["text"]

    owner_decides(vault, confirm=[TOOL])
    code, done = _run(capsys, "run", "process_released", *common)
    assert code == 0 and done["status"] == "done"
    assert hooks.on("/discord/SECRET")[1].body["content"].startswith("**Release run**\n")
    assert "**Processed (1):**" in hooks.on("/discord/SECRET")[1].body["content"]
    assert len(hooks.on("/teams/SECRET")) == 2


def test_a_failing_channel_does_not_fail_the_run_or_block_the_other_channel(world, capsys):
    """US1-AS2 through the whole stack."""
    _, hooks, common = world
    hooks.status["/discord/"] = 500
    code, triage = _run(capsys, "run", "triage", *common)
    assert code == 0 and triage["status"] == "done"
    assert len(hooks.on("/teams/SECRET")) == 1
    assert triage["outputs"]["run_report"]["outputs"]["notify"]["failed"] == [
        {"channel": "discord_main", "error": "HTTP 500"}
    ]
    assert triage["outputs"]["run_report"]["outputs"]["notify"]["sent"] == ["teams_main"]
