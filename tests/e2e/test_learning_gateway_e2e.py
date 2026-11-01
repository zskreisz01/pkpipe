"""Spec 012 end to end: a confirmed learning item becomes a notebook with a podcast.

Entry point is ``pkpipe run`` (CLI). The vault is served by the real obsidian-vault MCP server and the
notebook by the real learning-gateway MCP server (both subprocesses, Streamable HTTP) on a synthetic temp
vault; only the model and the notifier are fakes. The gateway backend comes from overlay config (US2).

Covers US1, US2, FR-001, FR-002, FR-003, SC-001 and constitution IV (URL check before anything is forwarded).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pkpipe import app
from pkpipe.cli import main
from pkpipe.domain.triage import item_id_for
from pkpipe.obsidian import lists
from tests.fakes import FakeModel, FakeNotify
from tests.support.learning_mcp import http_server as learning_server
from tests.support.learning_mcp import write_overlay
from tests.support.pack_runtime import PACK_ROUTES, TRIAGE_FILE, fake_model_reply, owner_decides
from tests.support.vault_mcp import free_port, write_policy
from tests.support.vault_mcp import http_server as vault_server

VIDEO = item_id_for("0-INBOX/example-video.md")
BAD = item_id_for("0-INBOX/bad-link.md")
DENIED = item_id_for("0-INBOX/denied-link.md")
PLIST = "0-INBOX/Manual-Resource-Processing-List.md"
FAST_WAIT = {
    "flows": {"learning_material": {"nodes": [{"id": "wait_generation", "config": {"poll_s": 0.05}}]}}
}


@pytest.fixture
def fakes(monkeypatch):
    model = FakeModel(fake_model_reply({"video": "learning-material"}))
    notify = FakeNotify(routes=PACK_ROUTES)
    monkeypatch.setattr(app, "make_model", lambda snapshot: model)
    monkeypatch.setattr(app, "make_notify", lambda snapshot: notify)
    monkeypatch.delenv("PKPIPE_OVERLAY_DIR", raising=False)
    return notify


@pytest.fixture
def world(tmp_path, synthetic_vault, write_json, fakes, capsys, request):
    learning = getattr(request, "param", {})
    (synthetic_vault / "0-INBOX" / "example-tool.md").unlink()  # only the learning items
    (synthetic_vault / "0-INBOX" / "bad-link.md").write_text(
        "---\nsource: http://127.0.0.1:8080/admin\n---\nA synthetic video pointing at a private address.\n",
        encoding="utf-8",
    )
    (synthetic_vault / "0-INBOX" / "denied-link.md").write_text(
        "---\nsource: https://talks.denied.example.invalid/video\n---\nA synthetic video on a denied host.\n",
        encoding="utf-8",
    )
    gateway_overlay = write_overlay(tmp_path, **learning)
    with vault_server(write_policy(tmp_path, synthetic_vault), free_port()) as vault_url:
        with learning_server(gateway_overlay) as learning_url:
            overlay = tmp_path / "overlay"
            write_json(
                overlay,
                "mcp_servers.json",
                {"obsidian": {"url": vault_url}, "learning": {"url": learning_url}},
            )
            write_json(overlay, "flows/learning_material.json", FAST_WAIT["flows"]["learning_material"])
            common = ("--overlay", str(overlay), "--db", str(tmp_path / "app.db"), "--json")

            def run(*argv):
                code = main(list(argv) + list(common))
                lines = capsys.readouterr().out.strip().splitlines()
                return code, json.loads(lines[-1])

            yield synthetic_vault, run, fakes, tmp_path / "learning-data"


def jobs(data_dir: Path) -> list[dict]:
    with sqlite3.connect(data_dir / "jobs.db") as db:
        return [json.loads(r[0]) for r in db.execute("select data from notebook_jobs")]


def release(run, vault, confirm):
    code, triage = run("run", "triage")
    assert code == 0, triage
    owner_decides(vault, confirm=confirm)
    code, out = run("run", "process_released")
    assert code == 0 and out["status"] == "done", out
    return out


@pytest.mark.parametrize(
    "world",
    [
        {"backend_options": {"ready_after": 3}},
        {
            "backend": "notebooklm_browser",
            "backend_options": {
                "driver": "tests.support.notebooklm_driver:make",
                "options": {"ready_after": 3},
            },
        },
    ],
    ids=["fake", "notebooklm_browser"],
    indirect=True,
)
def test_confirmed_learning_item_becomes_a_ready_notebook_whatever_the_backend(world, request):
    """US1-AS1/AS2 and US2-AS1: same flow, same tools; only the overlay differs between the two backends."""
    vault, run, notify, data_dir = world
    release(run, vault, confirm=[VIDEO])
    rows = {r["item_id"]: r["state"] for r in lists.read_triage(vault / TRIAGE_FILE)["rows"]}
    assert rows[VIDEO] == "processed"
    plist = {r.item_id: r for r in lists.read_processing_list(vault / PLIST)}
    row = plist[VIDEO]
    assert row.status.value == "ready" and row.category == "learning-material"
    assert row.nb_id and row.link.startswith("https://")  # notebook id and link are stored in the list
    [job] = jobs(data_dir)
    assert job["status"] == "ready" and job["item_id"] == VIDEO and job["nb_id"] == row.nb_id
    assert job["backend"] == request.node.callspec.id  # the overlay's backend ran, not another one
    assert Path(job["audio_path"]).is_file() and Path(job["audio_path"]).stat().st_size > 0  # audio stored
    assert notify.sent[-1].message.startswith("Processed 1, quarantined 0, failed 0")


@pytest.mark.parametrize("world", [{}], indirect=True)
def test_a_private_source_url_is_quarantined_before_anything_reaches_the_gateway(world):
    """Constitution IV, BPMN URL check -> Security flag: no job is created for a blocked URL."""
    vault, run, notify, data_dir = world
    release(run, vault, confirm=[BAD])
    rows = {r["item_id"]: r["state"] for r in lists.read_triage(vault / TRIAGE_FILE)["rows"]}
    assert rows[BAD] == "quarantined"
    assert not (data_dir / "jobs.db").exists() or jobs(data_dir) == []
    assert BAD not in {r.item_id for r in lists.read_processing_list(vault / PLIST)}
    assert notify.sent[-1].message.startswith("Processed 0, quarantined 1, failed 0")


@pytest.mark.parametrize("world", [{"deny": ["denied.example.invalid"]}], indirect=True)
def test_a_url_the_gateway_refuses_quarantines_the_item_instead_of_retrying_it(world):
    """SC-003, constitution IV: the flow's first check passes a public-looking URL, the gateway refuses it
    (deny list, also DNS to a private address) with ``url_blocked: ...``; the item is quarantined with a
    security flag, no job and no notebook exist, and it is not left ``released`` for endless retries."""
    vault, run, notify, data_dir = world
    release(run, vault, confirm=[DENIED])
    rows = {r["item_id"]: r["state"] for r in lists.read_triage(vault / TRIAGE_FILE)["rows"]}
    assert rows[DENIED] == "quarantined"
    assert not (data_dir / "jobs.db").exists() or jobs(data_dir) == []
    assert DENIED not in {r.item_id for r in lists.read_processing_list(vault / PLIST)}
    assert notify.sent[-1].message.startswith("Processed 0, quarantined 1, failed 0")


@pytest.mark.parametrize("world", [{"backend_options": {"fail": []}, "timeout_s": 0.001}], indirect=True)
def test_a_generation_that_exceeds_the_gateway_timeout_is_listed_as_failed_with_its_reason(world):
    """SC-001 (timeout), FR-003: the item is failed, not stuck; the reason is recorded in the job store."""
    vault, run, notify, data_dir = world
    release(run, vault, confirm=[VIDEO])
    plist = {r.item_id: r for r in lists.read_processing_list(vault / PLIST)}
    assert plist[VIDEO].status.value == "failed"
    [job] = jobs(data_dir)
    assert job["status"] == "failed" and job["reason"].startswith("timeout after")


@pytest.mark.parametrize("world", [{"backend_options": {"never": [VIDEO]}}], indirect=True)
def test_a_stalled_generation_ends_with_a_failed_item_while_the_gateway_keeps_its_job(world):
    """The wait deadline backs up the gateway timeout; the item is failed and its single job is kept."""
    vault, run, notify, data_dir = world
    code, triage = run("run", "triage")
    assert code == 0
    owner_decides(vault, confirm=[VIDEO])
    # the flow's own deadline (no gateway timeout in reach): shrink it through the overlay
    overlay_flow = Path(vault).parent / "overlay" / "flows" / "learning_material.json"
    overlay_flow.write_text(
        json.dumps({"nodes": [{"id": "wait_generation", "config": {"poll_s": 0.05, "deadline_s": 0.3}}]}),
        encoding="utf-8",
    )
    code, out = run("run", "process_released")
    assert code == 0, out
    plist = {r.item_id: r for r in lists.read_processing_list(vault / PLIST)}
    assert plist[VIDEO].status.value == "failed"
    assert [j["status"] for j in jobs(data_dir)] == ["generating"]  # the gateway still tracks the job
