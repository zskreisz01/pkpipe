"""Spec 015 (US1, US2, FR-001, FR-002): the self-improvement cycle through the CLI against the real
obsidian-vault MCP server (subprocess, Streamable HTTP) on a synthetic vault with synthetic journals.

Only the model and the notifier are fakes at the composition root. An overlay enables the two schedules,
as the owner would."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastmcp import Client

from pkpipe import app
from pkpipe.cli import main
from tests.fakes import FakeModel, FakeNotify
from tests.support.improvement import (
    BACKLOG,
    ROUTES,
    ImprovementModel,
    backlog,
    journal_with,
    owner_sets_status,
    write_journal,
)
from tests.support.vault_mcp import free_port, http_server, write_policy

DAY = "2026-10-30"
STRETCH = "Stretch for ten minutes every morning."


@pytest.fixture
def served(tmp_path, synthetic_vault, write_json):
    (synthetic_vault / "2-Areas" / "Journal").mkdir(parents=True)
    with http_server(write_policy(tmp_path, synthetic_vault), free_port()) as url:
        overlay = tmp_path / "overlay"
        write_json(overlay, "mcp_servers.json", {"obsidian": {"url": url}})
        write_json(
            overlay,
            "schedules.json",
            [
                {"cron": "30 21 * * *", "flow": "improvement_extract"},
                {"cron": "30 19 * * 0", "flow": "improvement_review"},
            ],
        )
        yield synthetic_vault, overlay, tmp_path / "app.db", url


def _model():
    def extract(user):
        return {
            "items": [{"statement": STRETCH, "area": "personal", "type": "habit", "success_criterion": ""}]
            if STRETCH in user
            else []
        }

    evidence = lambda user: {  # noqa: E731
        "evidence": [{"quote": "Stretched before breakfast, felt great.", "signal": "supports"}]
    }
    verdict = lambda user: {  # noqa: E731
        "verdict": "keep",
        "reason": "It works.",
        "next_experiment": "Add a second session.",
    }
    return ImprovementModel(extract=extract, evidence=evidence, verdict=verdict)


def _run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr().out.strip().splitlines()
    return code, json.loads(out[-1]) if out else {}


def test_validate_accepts_the_overlay_that_enables_the_schedules(served, capsys):
    vault, overlay, db, _ = served
    code = main(["validate", "--overlay", str(overlay), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert not [w for w in out.get("warnings", []) if "improvement" in w]


def test_extract_then_owner_then_weekly_review_through_the_cli(served, capsys, monkeypatch):
    vault, overlay, db, _ = served
    model, notify = _model(), FakeNotify(routes=ROUTES)
    monkeypatch.setattr(app, "make_model", lambda snapshot: FakeModel(model, schema_check=True))
    monkeypatch.setattr(app, "make_notify", lambda snapshot: notify)
    monkeypatch.delenv("PKPIPE_OVERLAY_DIR", raising=False)
    common = ("--overlay", str(overlay), "--db", str(db), "--json")
    write_journal(vault, DAY, journal_with(f"- {STRETCH}\n", extra="PRIVATE-DIARY-LINE"))

    code, out = _run(capsys, "run", "improvement_extract", *common, "--input", json.dumps({"date": DAY}))

    assert code == 0 and out["status"] == "done", out
    [(item_id, row)] = backlog(vault).items()
    assert row["status"] == "proposed" and row["statement"] == STRETCH
    assert model.kinds == ["items"]

    # the weekly review ignores a proposed item; the owner activates it in Obsidian
    code, out = _run(
        capsys, "run", "improvement_review", *common, "--input", json.dumps({"date": "2026-11-01"})
    )
    assert code == 0 and notify.sent == [] and model.kinds == ["items"]
    owner_sets_status(vault, item_id, "active")
    write_journal(vault, "2026-10-31", "# Journal\n\nStretched before breakfast, felt great.\n")

    code, out = _run(
        capsys, "run", "improvement_review", *common, "--input", json.dumps({"date": "2026-11-01"})
    )

    assert code == 0 and out["status"] == "done", out
    row = backlog(vault)[item_id]
    assert row["status"] == "active" and row["verdict"] == "keep"
    assert row["evidence"] == '2026-10-31 (+) "Stretched before breakfast, felt great."'
    assert model.kinds == ["items", "evidence", "verdict"]
    [sent] = notify.sent
    assert sent.event == "weekly_review" and STRETCH not in sent.message and "1 keep" in sent.message
    code, runs = _run(capsys, "runs", "--db", str(db), "--json")
    assert [(r["flow_id"], r["status"]) for r in runs] == [("improvement_extract", "done")] + [
        ("improvement_review", "done")
    ] * 2


def test_the_vault_server_refuses_status_changes_by_the_pipeline(served):
    """FR-001 over MCP: through the tool surface the agent can propose, never activate or keep."""
    vault, overlay, db, url = served

    async def go():
        async with Client(url) as c:
            new = {
                "source_note": f"2-Areas/Journal/{DAY}.md",
                "date": DAY,
                "statement": STRETCH,
                "area": "personal",
                "type": "habit",
            }
            ok = await c.call_tool("update_improvements", {"rows": [new]}, raise_on_error=False)
            assert not ok.is_error
            bad_new = await c.call_tool(
                "update_improvements",
                {"rows": [{**new, "statement": "Another.", "status": "active"}]},
                raise_on_error=False,
            )
            item_id = ok.data["inserted"][0]
            bad_edit = await c.call_tool(
                "update_improvements", {"rows": [{"id": item_id, "status": "kept"}]}, raise_on_error=False
            )
            bad_write = await c.call_tool(
                "write_note",
                {"path": "2-Areas/Self-Improvement/Improvement-Backlog.md", "body": "x", "overwrite": True},
                raise_on_error=False,
            )
            notes = await c.call_tool(
                "read_notes", {"paths": [f"2-Areas/Journal/{DAY}.md"]}, raise_on_error=False
            )
            return bad_new, bad_edit, bad_write, notes

    bad_new, bad_edit, bad_write, notes = asyncio.run(go())
    assert bad_new.is_error and "proposed" in bad_new.content[0].text
    assert bad_edit.is_error and "owner" in bad_edit.content[0].text
    assert bad_write.is_error
    assert not notes.is_error and notes.data["missing"] == [f"2-Areas/Journal/{DAY}.md"]
    assert [r["status"] for r in backlog(vault).values()] == ["proposed"]
    assert (vault / BACKLOG).exists()
