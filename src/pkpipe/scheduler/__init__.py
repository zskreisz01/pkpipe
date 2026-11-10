"""Scheduled triggers from ``schedules.json`` (spec 005 US3, FR-005, T010).

APScheduler 3 with a SQLite job store (the app DB). Every schedule becomes the job ``flow:<flow>`` with
``max_instances=1`` (one run per flow at a time) and ``coalesce=True`` (several missed fire times run once).
A run missed while the machine slept still starts once when it wakes within ``misfire_grace_s``.

:meth:`FlowScheduler.sync` is idempotent: unchanged schedules keep their stored next fire time, so a restart
of the app does not drop a missed run; changed schedules are replaced, removed or disabled ones deleted.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apscheduler.jobstores.base import BaseJobStore
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_STOPPED, BaseScheduler
from apscheduler.triggers.cron import CronTrigger

from pkpipe.engine.config_models import Schedule

log = logging.getLogger("pkpipe.scheduler")
JOB_PREFIX = "flow:"

#: scheduler name -> callback(flow_id). Jobs store only (name, flow) so they survive in the SQLite job store.
_RUNNERS: dict[str, Callable[[str], Any]] = {}


def fire(scheduler_name: str, flow_id: str) -> None:
    """APScheduler job function (referenced by import path in the persistent job store)."""
    runner = _RUNNERS.get(scheduler_name)
    if runner is None:
        log.warning("no runner registered for scheduler '%s'; skipping flow '%s'", scheduler_name, flow_id)
        return
    runner(flow_id)


@dataclass
class SyncReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def next_fire_times(
    schedules: Iterable[Mapping[str, Any]], now: datetime | None = None
) -> list[dict[str, str]]:
    """The next fire time of every enabled schedule, soonest first (spec 033: the overview shows when the
    next triage and release run start). ``at`` is UTC ``YYYY-MM-DD HH:MM``; a bad schedule is skipped."""
    now = now or datetime.now(UTC)
    out: list[tuple[datetime, dict[str, str]]] = []
    for raw in schedules or []:
        try:
            s = Schedule.model_validate(dict(raw))
            if not s.enabled:
                continue
            trigger = (
                CronTrigger.from_crontab(s.cron, timezone=s.timezone)
                if s.timezone
                else (CronTrigger.from_crontab(s.cron))
            )
            nxt = trigger.get_next_fire_time(None, now)
        except Exception:  # noqa: BLE001 - one broken schedule must not hide the others
            continue
        if nxt is not None:
            out.append(
                (nxt, {"flow": s.flow, "cron": s.cron, "at": nxt.astimezone(UTC).strftime("%Y-%m-%d %H:%M")})
            )
    return [row for _, row in sorted(out, key=lambda x: x[0])]


def sqlite_jobstore(path: Path) -> BaseJobStore:
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return SQLAlchemyJobStore(url=f"sqlite:///{Path(path)}", tablename="apscheduler_jobs")


class FlowScheduler:
    """Fires ``run_flow(flow_id)`` (normally ``RunService.start(flow, trigger="schedule")``) on cron times."""

    def __init__(
        self,
        run_flow: Callable[[str], Any],
        *,
        db_path: Path | None = None,
        scheduler: BaseScheduler | None = None,
        name: str = "default",
        timezone: Any = None,
    ):
        self.name = name
        _RUNNERS[name] = run_flow
        store = sqlite_jobstore(db_path) if db_path else MemoryJobStore()
        if scheduler is None:
            scheduler = BackgroundScheduler(**({"timezone": timezone} if timezone else {}))
        # configure() resets unspecified options, so the zone is passed again
        scheduler.configure(jobstores={"default": store}, timezone=timezone or scheduler.timezone)
        self.scheduler = scheduler

    def start(self, schedules: Iterable[Mapping[str, Any]] = (), known_flows: Iterable[str] | None = None):
        """Start paused, sync the jobs, then resume, so missed runs are judged against the synced jobs."""
        if self.scheduler.state == STATE_STOPPED:
            self.scheduler.start(paused=True)
        report = self.sync(schedules, known_flows)
        self.scheduler.resume()
        return report

    def shutdown(self, wait: bool = True) -> None:
        if self.scheduler.state != STATE_STOPPED:
            self.scheduler.shutdown(wait=wait)
        _RUNNERS.pop(self.name, None)

    def jobs(self) -> dict[str, Any]:
        """flow id -> next fire time (None while paused)."""
        return {
            j.id.removeprefix(JOB_PREFIX): j.next_run_time
            for j in self.scheduler.get_jobs()
            if j.id.startswith(JOB_PREFIX)
        }

    def sync(
        self, schedules: Iterable[Mapping[str, Any]], known_flows: Iterable[str] | None = None
    ) -> SyncReport:
        report = SyncReport()
        known = set(known_flows) if known_flows is not None else None
        desired: dict[str, Schedule] = {}
        for raw in schedules:
            s = Schedule.model_validate(dict(raw))
            if s.flow in desired:
                raise ValueError(f"two schedules for flow '{s.flow}'")
            if not s.enabled:
                continue
            if known is not None and s.flow not in known:
                log.warning("schedule for unknown flow '%s' skipped", s.flow)
                report.skipped.append(s.flow)
                continue
            desired[s.flow] = s
        existing = {j.id: j for j in self.scheduler.get_jobs() if j.id.startswith(JOB_PREFIX)}
        for flow, s in desired.items():
            job_id = JOB_PREFIX + flow
            trigger = CronTrigger.from_crontab(s.cron, timezone=s.timezone or self.scheduler.timezone)
            job = existing.get(job_id)
            if (
                job is not None
                and str(job.trigger) == str(trigger)
                and str(getattr(job.trigger, "timezone", "")) == str(trigger.timezone)
                and job.misfire_grace_time == s.misfire_grace_s
                and tuple(job.args) == (self.name, flow)
            ):
                report.unchanged.append(flow)
                continue
            self.scheduler.add_job(
                fire,
                trigger,
                args=[self.name, flow],
                id=job_id,
                name=flow,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=s.misfire_grace_s,
            )
            (report.updated if job is not None else report.added).append(flow)
        for job_id in existing:
            flow = job_id.removeprefix(JOB_PREFIX)
            if flow not in desired:
                self.scheduler.remove_job(job_id)
                report.removed.append(flow)
        return report


__all__ = ["JOB_PREFIX", "FlowScheduler", "SyncReport", "fire", "sqlite_jobstore"]
