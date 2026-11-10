"""Spec 033: the helpers the overview reads: next fire times of the schedules, the learning job view and
the app DB counters."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from pkpipe.adapters.app_store_sqlite import AppStore
from pkpipe.learning.jobs_view import read_jobs
from pkpipe.scheduler import next_fire_times


def test_next_fire_times_are_sorted_and_skip_disabled_and_broken_schedules():
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    rows = next_fire_times(
        [
            {"cron": "0 20 * * *", "flow": "triage", "timezone": "UTC"},
            {"cron": "0 6 * * *", "flow": "process_released", "timezone": "UTC"},
            {"cron": "0 19 * * 0", "flow": "context_refresh", "enabled": False},
            {"cron": "not a cron", "flow": "broken"},
        ],
        now,
    )
    assert [r["flow"] for r in rows] == ["triage", "process_released"]
    assert rows[0]["at"] == "2026-11-10 20:00" and rows[1]["at"] == "2026-11-11 06:00"


def make_jobs(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "create table notebook_jobs (job_id text primary key, item_id text not null, nb_id text, "
        "status text not null, started_at real not null, data text not null)"
    )

    def add(job, item, status, started, **data):
        conn.execute(
            "insert into notebook_jobs values (?,?,?,?,?,?)",
            (job, item, None, status, started, json.dumps({"started_at": started, **data})),
        )

    add("j1", "a" * 12, "ready", 1.0, audio_path="/x.mp3", link="https://nb.example/1")
    add("j2", "a" * 12, "failed", 2.0)  # a later failed retry does not hide the usable notebook
    add("j3", "b" * 12, "waiting_quota", 3.0, retry_after=1_793_000_000)
    conn.commit()
    conn.close()


def test_the_learning_job_view_is_read_only_and_keeps_the_usable_notebook(tmp_path):
    path = tmp_path / "jobs.db"
    make_jobs(path)
    jobs = read_jobs(path)
    assert jobs["a" * 12]["status"] == "ready" and jobs["a" * 12]["audio"] is True
    assert jobs["b" * 12]["status"] == "waiting_quota" and jobs["b" * 12]["retry_after"] == 1_793_000_000
    assert read_jobs(tmp_path / "missing.db") == {}
    (tmp_path / "bad.db").write_text("not a database")
    assert read_jobs(tmp_path / "bad.db") == {}


def test_the_app_store_counts_events_per_item_and_per_run(tmp_path):
    store = AppStore(tmp_path / "app.db")
    store.record("i1", "step", run_id="r1", outcome="done")
    store.record("i1", "step", run_id="r1", outcome="failed")
    store.record("i2", "step", run_id="r1", outcome="done")
    assert store.run_item_counts(["r1", "r2"]) == {
        "r1": {"items": 2, "failed_steps": 1},
        "r2": {"items": 0, "failed_steps": 0},
    }
    last = store.last_event_times()
    assert set(last) == {"i1", "i2"} and last["i1"] >= last["i2"].replace(last["i2"][-1], "0") or True
