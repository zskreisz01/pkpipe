"""Spec 005 US3 / FR-005 / T010: schedules.json -> APScheduler jobs (SQLite job store), fake clock."""

from __future__ import annotations

from datetime import datetime

import pytest

from pkpipe.scheduler import FlowScheduler
from tests.fakes import FakeTools
from tests.support.flows import make_ports, make_service, make_snapshot
from tests.support.scheduling import UTC, ManualScheduler, fake_time

TRIAGE = [{"cron": "0 20 * * *", "flow": "triage"}]


def _scheduler(started: list, db=None, name="t"):
    return FlowScheduler(started.append, db_path=db, scheduler=ManualScheduler(timezone=UTC), name=name)


def test_daily_cron_starts_the_flow_once_when_the_time_passes(tmp_path):
    """US3-AS1: given 0 20 * * *, when 20:00 passes, triage starts once."""
    started: list[str] = []
    with fake_time(datetime(2026, 11, 2, 19, 59, tzinfo=UTC)) as clock:
        s = _scheduler(started, tmp_path / "app.db")
        report = s.start(TRIAGE, known_flows=["triage"])
        assert report.added == ["triage"]
        s.scheduler.tick()
        assert started == []
        clock.advance(minutes=1, seconds=1)
        s.scheduler.tick()
        s.scheduler.tick()
        assert started == ["triage"]
        clock.advance(days=1)
        s.scheduler.tick()
        assert started == ["triage", "triage"]
        s.shutdown()


def test_missed_runs_after_sleep_run_once_within_grace():
    """US3-AS2: the machine slept over several fire times; on wake within grace the flow runs once."""
    started: list[str] = []
    every_15 = [{"cron": "*/15 * * * *", "flow": "triage", "misfire_grace_s": 3 * 3600}]
    with fake_time(datetime(2026, 11, 2, 10, 1, tzinfo=UTC)) as clock:
        s = _scheduler(started)
        s.start(every_15)
        clock.advance(hours=2)  # asleep: eight fire times missed
        s.scheduler.tick()
        assert started == ["triage"]  # coalesce=True
        s.scheduler.tick()
        assert started == ["triage"]
        s.shutdown()


def test_a_run_missed_beyond_the_grace_time_is_skipped():
    started: list[str] = []
    with fake_time(datetime(2026, 11, 2, 19, 0, tzinfo=UTC)) as clock:
        s = _scheduler(started)
        s.start([{"cron": "0 20 * * *", "flow": "triage", "misfire_grace_s": 600}])
        clock.advance(hours=3)  # woke at 22:00, 2 h after the fire time, grace is 10 min
        s.scheduler.tick()
        assert started == []
        assert s.jobs()["triage"] == datetime(2026, 11, 3, 20, 0, tzinfo=UTC)
        s.shutdown()


def test_missed_run_survives_an_app_restart_via_the_sqlite_job_store(tmp_path):
    """FR-005: jobs live in SQLite; re-syncing unchanged schedules keeps their next fire time, so a run missed
    while the app was down still happens once."""
    db = tmp_path / "app.db"
    started: list[str] = []
    with fake_time(datetime(2026, 11, 2, 19, 0, tzinfo=UTC)) as clock:
        first = _scheduler(started, db, name="app")
        first.start(TRIAGE)
        first.shutdown()  # app stops at 19:00
        clock.advance(hours=2)  # comes back at 21:00
        second = _scheduler(started, db, name="app")
        report = second.start(TRIAGE)
        assert report.unchanged == ["triage"] and report.added == []
        second.scheduler.tick()
        assert started == ["triage"]
        second.shutdown()


def test_jobs_have_one_instance_per_flow_and_coalesce(tmp_path):
    started: list[str] = []
    with fake_time(datetime(2026, 11, 2, 12, 0, tzinfo=UTC)):
        s = _scheduler(started, tmp_path / "app.db")
        s.start(
            [
                {"cron": "0 20 * * *", "flow": "triage"},
                {"cron": "0 6 * * *", "flow": "process_released", "misfire_grace_s": 900},
            ]
        )
        jobs = {j.id: j for j in s.scheduler.get_jobs()}
        assert set(jobs) == {"flow:triage", "flow:process_released"}
        for job in jobs.values():
            assert job.max_instances == 1 and job.coalesce is True
        assert jobs["flow:process_released"].misfire_grace_time == 900
        assert jobs["flow:triage"].misfire_grace_time == 21600
        s.shutdown()


def test_sync_updates_removes_disables_and_skips_unknown_flows():
    started: list[str] = []
    with fake_time(datetime(2026, 11, 2, 12, 0, tzinfo=UTC)):
        s = _scheduler(started)
        s.start([{"cron": "0 20 * * *", "flow": "triage"}, {"cron": "0 6 * * *", "flow": "process_released"}])
        report = s.sync(
            [
                {"cron": "30 21 * * *", "flow": "triage"},
                {"cron": "0 6 * * *", "flow": "process_released", "enabled": False},
                {"cron": "0 19 * * 0", "flow": "context_refresh"},
            ],
            known_flows=["triage", "process_released"],
        )
        assert report.updated == ["triage"] and report.removed == ["process_released"]
        assert report.skipped == ["context_refresh"]
        assert s.jobs() == {"triage": datetime(2026, 11, 2, 21, 30, tzinfo=UTC)}
        s.shutdown()


def test_two_schedules_for_the_same_flow_are_rejected():
    """Edge case: two schedules for the same flow -> validation error."""
    s = _scheduler([])
    with pytest.raises(ValueError, match="two schedules for flow 'triage'"):
        s.sync([*TRIAGE, {"cron": "0 21 * * *", "flow": "triage"}])
    s.shutdown()


def test_scheduled_trigger_records_a_schedule_run():
    """US3 + FR-004: the scheduler starts runs through the run service with trigger 'schedule'."""
    flow = {
        "id": "triage",
        "start": "a",
        "nodes": [{"id": "a", "type": "tool", "config": {"tool": "t:read"}}],
    }
    svc = make_service(make_snapshot(flow), make_ports(tools=FakeTools({"t:read": lambda: {"ok": True}})))
    outcomes = []
    with fake_time(datetime(2026, 11, 2, 19, 59, tzinfo=UTC)) as clock:
        s = FlowScheduler(
            lambda f: outcomes.append(svc.start(f, trigger="schedule")),
            scheduler=ManualScheduler(timezone=UTC),
            name="svc",
        )
        s.start(TRIAGE)
        clock.advance(minutes=2)
        s.scheduler.tick()
        s.shutdown()
    [outcome] = outcomes
    assert outcome.ok and svc.get(outcome.run.run_id).trigger.value == "schedule"
