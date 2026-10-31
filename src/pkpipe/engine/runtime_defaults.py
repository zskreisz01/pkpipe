"""Stdlib defaults for the clock and tracer ports, used when no adapter is wired (tests, library use)."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
from typing import Any


class SystemClock:
    """ClockPort on the wall clock."""

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class NullSpan:
    def set(self, key: str, value: Any) -> None:
        pass

    def event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        pass

    def fail(self, error_type: str) -> None:
        pass


class NullTracer:
    """TracerPort that records nothing (the OpenTelemetry adapter is ``pkpipe.adapters.tracer_otel``)."""

    @contextlib.contextmanager
    def _span(self, *args: Any, **kwargs: Any) -> Iterator[NullSpan]:
        yield NullSpan()

    flow = item = step = llm = tool = _span

    def capture(self, **content: str) -> bool:
        return False

    def security_flag(self, detector: str, *, action: str = "flag", reason_code: str | None = None) -> None:
        pass
