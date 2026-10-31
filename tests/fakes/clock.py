from __future__ import annotations

import threading


class FakeClock:
    """ClockPort fake: time only moves when someone sleeps (or a test calls ``advance``)."""

    def __init__(self, start: float = 1_800_000_000.0):
        self._now = start
        self._lock = threading.Lock()
        self.sleeps: list[float] = []

    def now(self) -> float:
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.sleeps.append(seconds)
            self._now += max(0.0, seconds)

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += seconds
