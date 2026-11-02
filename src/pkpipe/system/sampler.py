"""A background thread that re-samples the system during a long run (live tests) and keeps the decision
current, so a thermal or memory degradation in the middle of a run lowers the concurrency of later evals."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from pkpipe.domain.system import THERMAL_LEVELS, PreflightDecision, SystemSnapshot, decide
from pkpipe.engine.config_models import SystemPreflight
from pkpipe.ports import SystemProbePort
from pkpipe.system.preflight import Preflight, record
from pkpipe.telemetry import Telemetry

_GiB = 1024**3


@dataclass
class SamplerSummary:
    """What happened while sampling: swap range, thermal events, concurrency changes."""

    samples: int = 0
    started_at: float = 0.0
    ended_at: float = 0.0
    swap_min: int | None = None
    swap_max: int | None = None
    swap_total: int = 0
    worst_pressure: str | None = None
    max_load_per_cpu: float | None = None
    thermal_events: list[str] = field(default_factory=list)
    concurrency_changes: list[str] = field(default_factory=list)
    errors: int = 0

    def lines(self) -> list[str]:
        mins = (self.ended_at - self.started_at) / 60
        out = [f"{self.samples} sample(s) over {mins:.1f} min"]
        if self.swap_max is not None:
            out.append(
                f"swap used min {self.swap_min / _GiB:.2f} / max {self.swap_max / _GiB:.2f} GiB "
                f"(of {self.swap_total / _GiB:.2f} GiB)"
            )
        if self.worst_pressure:
            out.append(f"worst memory pressure: {self.worst_pressure}")
        if self.max_load_per_cpu is not None:
            out.append(f"max load per CPU: {self.max_load_per_cpu:g}")
        out.append("thermal events: " + ("; ".join(self.thermal_events) if self.thermal_events else "none"))
        out.append(
            "concurrency changes: "
            + ("; ".join(self.concurrency_changes) if self.concurrency_changes else "none")
        )
        if self.errors:
            out.append(f"sampling errors: {self.errors}")
        return out


class SystemSampler:
    """Samples every ``interval_s`` until :meth:`stop`. :attr:`decision` is the latest decision;
    ``on_change(decision)`` runs (in the sampler thread) whenever status, concurrency or timeout multiplier
    changes. Every sample records the ``pkpipe.system.*`` gauges; a change also records a
    ``system.preflight`` span (``system.source = sampler``)."""

    def __init__(
        self,
        probe: SystemProbePort,
        settings: SystemPreflight | None = None,
        *,
        required_models: Iterable[str] = (),
        max_concurrency: int | None = None,
        interval_s: float | None = None,
        telemetry: Telemetry | None = None,
        on_change: Callable[[PreflightDecision], None] | None = None,
        initial: Preflight | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.probe = probe
        self.settings = settings or SystemPreflight()
        self.required = tuple(required_models)
        self.max_concurrency = max_concurrency
        self.interval_s = interval_s if interval_s is not None else self.settings.sample_interval_s
        self.telemetry = telemetry
        self.on_change = on_change
        self.clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.summary = SamplerSummary(started_at=clock())
        self._decision: PreflightDecision | None = None
        self._thermal_state: tuple[str | None, bool] = ("nominal", False)
        if initial is not None:
            self._decision = initial.decision
            self._observe(initial.snapshot, self.summary.started_at)

    @property
    def decision(self) -> PreflightDecision | None:
        with self._lock:
            return self._decision

    def _observe(self, snap: SystemSnapshot, now: float) -> None:
        s = self.summary
        s.samples += 1
        s.ended_at = now
        used = snap.swap.used_bytes
        if snap.swap.total_bytes:
            s.swap_total = max(s.swap_total, snap.swap.total_bytes)
            s.swap_min = used if s.swap_min is None else min(s.swap_min, used)
            s.swap_max = used if s.swap_max is None else max(s.swap_max, used)
        order = ("normal", "warn", "critical")
        if snap.memory.pressure in order and (
            s.worst_pressure is None or order.index(snap.memory.pressure) > order.index(s.worst_pressure)
        ):
            s.worst_pressure = snap.memory.pressure
        if snap.load_per_cpu is not None:
            s.max_load_per_cpu = max(s.max_load_per_cpu or 0.0, snap.load_per_cpu)
        state = (snap.thermal.level, snap.thermal.throttled)
        if state != self._thermal_state and (state[0] in THERMAL_LEVELS[1:] or state[1] or s.thermal_events):
            minute = (now - s.started_at) / 60
            limit = f", CPU speed {snap.thermal.cpu_speed_limit}%" if snap.thermal.throttled else ""
            s.thermal_events.append(f"+{minute:.1f} min {state[0] or 'unknown'}{limit}")
        self._thermal_state = state

    def sample_once(self) -> PreflightDecision:
        """Take one sample now (also used by tests)."""
        try:
            snap = self.probe.snapshot()
        except Exception:  # noqa: BLE001 - a broken probe must not kill the run; keep the last decision
            with self._lock:
                self.summary.errors += 1
                current = self._decision
            if current is None:
                raise
            return current
        now = self.clock()
        previous = self.decision
        own = previous.concurrency if previous is not None else 0
        decision = decide(
            snap,
            self.settings,
            required_models=self.required,
            max_concurrency=self.max_concurrency,
            own_requests=own,
        )
        changed = previous is None or (
            (previous.status, previous.concurrency, previous.timeout_multiplier)
            != (decision.status, decision.concurrency, decision.timeout_multiplier)
        )
        with self._lock:
            self._observe(snap, now)
            self._decision = decision
            if changed and previous is not None:
                minute = (now - self.summary.started_at) / 60
                self.summary.concurrency_changes.append(
                    f"+{minute:.1f} min {previous.concurrency} -> {decision.concurrency} "
                    f"({decision.summary()})"
                )
        record(snap, decision, self.telemetry, span=changed, source="sampler")
        if changed and self.on_change is not None:
            self.on_change(decision)
        return decision

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.sample_once()
            except Exception:  # noqa: BLE001
                with self._lock:
                    self.summary.errors += 1

    def start(self) -> SystemSampler:
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="pkpipe-system-sampler", daemon=True)
            self._thread.start()
        return self

    def stop(self, timeout: float = 10.0) -> SamplerSummary:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        self.summary.ended_at = max(self.summary.ended_at, self.clock())
        return self.summary

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
