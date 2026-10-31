"""Fake clock for APScheduler 3 (spec 005 US3): a scheduler that only wakes when the test says so."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import apscheduler.executors.base as executors_base
import apscheduler.schedulers.base as schedulers_base
from apscheduler.executors.debug import DebugExecutor
from apscheduler.schedulers.base import BaseScheduler


class FakeTime:
    def __init__(self, start: datetime):
        self.now = start

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


@contextlib.contextmanager
def fake_time(start: datetime) -> Iterator[FakeTime]:
    """Patch ``datetime.now`` inside APScheduler (scheduler loop and misfire check) to a settable clock."""
    clock = FakeTime(start)

    class _Datetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now.astimezone(tz) if tz else clock.now.replace(tzinfo=None)

    originals = (schedulers_base.datetime, executors_base.datetime)
    schedulers_base.datetime = _Datetime
    executors_base.datetime = _Datetime
    try:
        yield clock
    finally:
        schedulers_base.datetime, executors_base.datetime = originals


class ManualScheduler(BaseScheduler):
    """No background thread: jobs are processed when :meth:`tick` is called; jobs run synchronously."""

    def shutdown(self, wait=True):
        super().shutdown(wait)

    def wakeup(self):
        pass

    def _create_default_executor(self):
        return DebugExecutor()

    def tick(self) -> None:
        self._process_jobs()


UTC = UTC
