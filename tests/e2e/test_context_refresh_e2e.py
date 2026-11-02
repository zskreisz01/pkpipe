"""Spec 011 (US1, US2, FR-001, FR-002) end to end: ``pkpipe run context_refresh`` through the CLI against the
real obsidian-vault MCP server (subprocess, Streamable HTTP) on a synthetic vault of invented project notes,
writing real files into a synthetic overlay (a git repository, like the owner's).

Only the model and the notifier are fakes at the composition root."""

from __future__ import annotations

import json
import subprocess

import pytest

from pkpipe import app
from pkpipe.cli import main
from pkpipe.domain import context_pack as cp
from pkpipe.security import query_guard
from tests.fakes import FakeModel, FakeNotify
from tests.support.context_packs import (
    BODY_ONLY,
    CLIENT,
    PERSON,
    PROJECT,
    ROUTES,
    SECOND_CLIENT,
    ContextModel,
    seed_vault,
)
from tests.support.vault_mcp import free_port, http_server, write_policy


@pytest.fixture
def served(tmp_path, synthetic_vault, write_json):
    seed_vault(synthetic_vault)
    with http_server(write_policy(tmp_path, synthetic_vault), free_port()) as url:
        overlay = tmp_path / "overlay"
        write_json(overlay, "mcp_servers.json", {"obsidian": {"url": url}})
        subprocess.run(["git", "init", "-q", str(overlay)], check=True)
        yield synthetic_vault, overlay, tmp_path / "app.db"


def _run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr().out.strip().splitlines()
    return code, json.loads(out[-1]) if out else {}


@pytest.fixture
def stubbed(monkeypatch):
    def install(model: ContextModel):
        notify = FakeNotify(routes=ROUTES)
        monkeypatch.setattr(app, "make_model", lambda snapshot: FakeModel(model, schema_check=True))
        monkeypatch.setattr(app, "make_notify", lambda snapshot: notify)
        monkeypatch.delenv("PKPIPE_OVERLAY_DIR", raising=False)
        return notify

    return install


def test_validate_accepts_the_shipped_flows_with_an_overlay(served, capsys):
    vault, overlay, db = served
    code = main(["validate", "--overlay", str(overlay), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert not [w for w in out.get("warnings", []) if "context_refresh" in w or "has no flows" in w]


def test_refresh_writes_the_packs_into_the_overlay_and_the_review_notes_into_the_vault(
    served, capsys, stubbed
):
    vault, overlay, db = served
    notify = stubbed(ContextModel(leaky=True, names=[CLIENT, PROJECT, PERSON, SECOND_CLIENT, BODY_ONLY]))

    code, out = _run(capsys, "run", "context_refresh", "--overlay", str(overlay), "--db", str(db), "--json")

    assert code == 0 and out["status"] == "done", out
    personal = (overlay / "contexts/personal.md").read_text(encoding="utf-8")
    enterprise = (overlay / "contexts/enterprise.md").read_text(encoding="utf-8")
    assert "Homelab" in personal and "PostgreSQL" in enterprise
    assert not any(
        n.lower() in enterprise.lower() for n in (CLIENT, PROJECT, PERSON, SECOND_CLIENT, BODY_ONLY)
    )
    terms = query_guard.parse_terms((overlay / "security/query_guard_terms.txt").read_text(encoding="utf-8"))
    assert {CLIENT, PROJECT, SECOND_CLIENT, "Falcon", "Brightwater"} <= set(terms)
    assert (vault / "2-Areas/Context-Review/personal.md").is_file()
    review = (vault / "2-Areas/Context-Review/enterprise.md").read_text(encoding="utf-8")
    assert "type: context_review" in review and not any(
        n.lower() in review.lower() for n in (CLIENT, PROJECT)
    )
    [sent] = notify.sent
    assert sent.event == "context_review" and "personal: created" in sent.message
    assert not any(n in sent.message for n in (CLIENT, PROJECT, PERSON, "Homelab"))


def test_a_manual_edit_survives_the_next_weekly_run_and_the_diff_is_proposed(served, capsys, stubbed):
    vault, overlay, db = served
    notify = stubbed(ContextModel())
    common = ("--overlay", str(overlay), "--db", str(db), "--json")
    assert _run(capsys, "run", "context_refresh", *common)[0] == 0
    pack = overlay / "contexts/personal.md"
    mine = pack.read_text(encoding="utf-8").replace("Homelab", "Homelab (edited by me)")
    pack.write_text(mine + f"\n{cp.MANUAL_MARKER}\nAlways prefer SQLite.\n", encoding="utf-8")
    (vault / "1-Projects/Personal/New-Idea.md").write_text(
        "---\nstatus: idea\n---\nA new idea with Rust.\n", encoding="utf-8"
    )
    notify.sent.clear()

    code, out = _run(capsys, "run", "context_refresh", *common)

    assert code == 0 and out["status"] == "done", out
    kept = pack.read_text(encoding="utf-8")
    assert "Homelab (edited by me)" in kept and "Always prefer SQLite." in kept
    proposal = (overlay / "contexts/personal.md.proposed").read_text(encoding="utf-8")
    assert "Homelab (edited by me)" not in proposal and "Always prefer SQLite." in proposal
    review = (vault / "2-Areas/Context-Review/personal.md").read_text(encoding="utf-8")
    assert "```diff" in review and "personal.md.proposed" in review
    assert "personal: proposed" in notify.sent[-1].message
    # a .proposed file is no context pack: the config loader never reads it as an asset
    code, listing = _run(capsys, "validate", "--overlay", str(overlay), "--json")
    assert code == 0 and not [w for w in listing["warnings"] if "personal" in w]


def test_the_refresh_is_scheduled_for_sunday_19_00_and_runnable_on_demand(capsys):
    code = main(["validate", "--json"])
    assert code == 0
    capsys.readouterr()
    from pkpipe.engine.registry import load_effective

    cfg = load_effective(None, None)
    [sched] = [s for s in cfg.data["schedules"] if s["flow"] == "context_refresh"]
    assert sched["cron"] == "0 19 * * 0"  # FR-001
