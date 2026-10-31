"""Spec 005 US2 / SC-002 / T009: a run killed during a wait step resumes after a restart and loses no item.

The run executes in a real subprocess on a SQLite app DB; the test kills it with SIGKILL while items are
waiting, publishes a config change, then starts a new process that resumes from the checkpoints.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import REPO

WORKER = REPO / "tests" / "support" / "flow_worker.py"

BATCH = {
    "id": "batch",
    "start": "list",
    "max_parallel": 2,
    "nodes": [
        {"id": "list", "type": "tool", "config": {"tool": "jobs:list"}},
        {"id": "each", "type": "subflow", "for_each": "list.items", "config": {"flow": "job"}},
    ],
    "edges": [{"from": "list", "to": "each"}],
}


def job_flow(tag: str) -> dict:
    return {
        "id": "job",
        "start": "wait",
        "nodes": [
            {
                "id": "wait",
                "type": "wait",
                "bpmn_element": "Wait for generation result",
                "config": {
                    "tool": "jobs:status",
                    "arguments": {"job": "${item.id}"},
                    "until": {"path": "result.ready", "truthy": True},
                    "poll_s": 0.1,
                    "deadline_s": 600,
                },
            },
            {
                "id": "finish",
                "type": "tool",
                "config": {"tool": "jobs:finish", "arguments": {"job": "${item.id}", "tag": tag}},
            },
        ],
        "edges": [{"from": "wait", "to": "finish"}],
    }


@pytest.fixture
def setup(tmp_path: Path):
    pack, work = tmp_path / "pack", tmp_path / "work"
    (pack / "flows").mkdir(parents=True)
    work.mkdir()
    (pack / "mcp_servers.json").write_text(
        json.dumps(
            {"jobs": {"transport": "stdio", "command": "unused", "allow": ["list", "status", "finish"]}}
        )
    )
    (pack / "security.json").write_text((REPO / "packs/learning-inbox/security.json").read_text())
    (pack / "flows" / "batch.json").write_text(json.dumps(BATCH))
    (pack / "flows" / "job.json").write_text(json.dumps(job_flow("v1")))
    return tmp_path / "app.db", pack, work


def _worker(db: Path, pack: Path, work: Path, mode: str, **kw):
    cmd = [sys.executable, str(WORKER), "--db", str(db), "--pack", str(pack), "--work", str(work), mode]
    return cmd, {"cwd": REPO, "env": {**os.environ, "PYTHONUNBUFFERED": "1"}, **kw}


def _run_row(db: Path) -> dict | None:
    if not db.exists():
        return None
    try:
        with sqlite3.connect(db, timeout=5) as c:
            c.row_factory = sqlite3.Row
            row = c.execute("SELECT * FROM runs WHERE run_id = 'restart-run'").fetchone()
            return dict(row) if row else None
    except sqlite3.OperationalError:
        return None


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_kill_during_wait_then_resume_loses_no_item_and_keeps_the_pinned_config(setup):
    """US2-AS1 + AS2, SC-002: SIGKILL while items wait; after restart every item finishes exactly once, with
    the config version the run started on, although a new version was published meanwhile."""
    db, pack, work = setup
    (work / "a.ready").touch()  # item 'a' completes before the crash; 'b' and 'c' are waiting
    cmd, kw = _worker(db, pack, work, "start")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            polls = _lines(work / "polls.log")
            row = _run_row(db)
            if (
                _lines(work / "done.log") == ["a v1"]
                and polls.count("b") >= 3
                and polls.count("c") >= 3
                and row
                and row["status"] == "waiting"
            ):
                break
            if proc.poll() is not None:
                pytest.fail(f"worker exited early: {proc.communicate()}")
            time.sleep(0.05)
        else:
            pytest.fail(f"run never reached the wait state: polls={_lines(work / 'polls.log')}")
    finally:
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
    assert proc.returncode == -signal.SIGKILL
    started = _run_row(db)
    assert started["status"] == "waiting" and started["ended_at"] is None

    # publish a new config while the app is down, and let the jobs finish
    (pack / "flows" / "job.json").write_text(json.dumps(job_flow("v2")))
    for job in ("b", "c"):
        (work / f"{job}.ready").touch()

    cmd, kw = _worker(db, pack, work, "resume")
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)
    assert done.returncode == 0, done.stderr
    [record] = json.loads(done.stdout)
    assert record["run_id"] == "restart-run" and record["status"] == "done"

    assert sorted(_lines(work / "done.log")) == ["a v1", "b v1", "c v1"]  # each item once, pinned tag
    final = _run_row(db)
    assert final["status"] == "done" and final["config_version"] == started["config_version"]


def test_reopened_database_resumes_in_process(setup):
    """T006: closing every connection and re-opening the SQLite file is enough to resume a run."""
    from tests.support.flow_worker import build

    db, pack, work = setup
    for job in ("a", "b", "c"):
        (work / f"{job}.ready").touch()
    svc = build(db, pack, work)

    class Stop(BaseException):
        """The process dies inside the first status poll (not an Exception: nothing handles it)."""

    tools = svc.ports_for(None).tools
    real = tools.tools["jobs:status"]
    tools.tools["jobs:status"] = lambda job: (_ for _ in ()).throw(Stop())
    with pytest.raises(Stop):
        svc.start("batch", run_id="restart-run")
    tools.tools["jobs:status"] = real
    svc.checkpointer.conn.close()
    assert _run_row(db)["status"] == "waiting"

    again = build(db, pack, work)  # fresh store + checkpointer on the same file
    [outcome] = again.resume_incomplete()
    assert outcome.ok, outcome.error
    assert sorted(_lines(work / "done.log")) == ["a v1", "b v1", "c v1"]
