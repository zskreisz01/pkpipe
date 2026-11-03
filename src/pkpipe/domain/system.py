"""System snapshot and the preflight policy: code decides how hard to drive the LLM server
(docs/operations.md).

A :class:`SystemSnapshot` is what a ``SystemProbePort`` reads (memory, swap, thermal state, load, top memory
consumers by process *name*, LLM server state). :func:`decide` maps it with the ``evals.preflight`` thresholds
to a :class:`PreflightDecision`: ``ok`` / ``degraded`` / ``blocked``, the eval concurrency to use and a model
timeout multiplier, with one :class:`Reason` per rule that fired. Pure and deterministic (constitution V).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from pkpipe.engine.config_models import SystemPreflight

THERMAL_LEVELS = ("nominal", "fair", "serious", "critical")  # NSProcessInfo.thermalState 0..3
PRESSURE_LEVELS = ("normal", "warn", "critical")  # kern.memorystatus_vm_pressure_level 1, 2, 4


class Status(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class MemoryState:
    total_bytes: int
    available_bytes: int | None = None
    free_bytes: int | None = None
    compressed_bytes: int | None = None
    pressure: str | None = None  # PRESSURE_LEVELS, None = unknown

    @property
    def available_pct(self) -> float | None:
        if self.available_bytes is None or not self.total_bytes:
            return None
        return round(100.0 * self.available_bytes / self.total_bytes, 1)


@dataclass(frozen=True)
class SwapState:
    total_bytes: int = 0
    used_bytes: int = 0

    @property
    def used_pct(self) -> float | None:
        return round(100.0 * self.used_bytes / self.total_bytes, 1) if self.total_bytes else None


@dataclass(frozen=True)
class ThermalState:
    level: str | None = None  # THERMAL_LEVELS, None = unknown
    cpu_speed_limit: int | None = None  # percent, 100 = not throttled (pmset -g therm)
    warning: bool = False  # pmset recorded a thermal warning level

    @property
    def throttled(self) -> bool:
        return self.cpu_speed_limit is not None and self.cpu_speed_limit < 100


@dataclass(frozen=True)
class ProcessUsage:
    """A process *name* (never arguments or paths) and its resident memory, summed over same-named
    processes."""

    name: str
    rss_bytes: int


@dataclass(frozen=True)
class LlmModel:
    id: str
    loaded: bool | None = None  # None = the server does not say
    parallel: int | None = None
    context_length: int | None = None
    status: str | None = None  # idle | generating | ...
    queued: int | None = None


@dataclass(frozen=True)
class LlmServerState:
    base_url: str
    reachable: bool
    models: tuple[LlmModel, ...] = ()
    sources: tuple[str, ...] = ()  # openai | lmstudio-rest | lms
    error: str | None = None

    def model(self, model_id: str) -> LlmModel | None:
        return next((m for m in self.models if m.id == model_id), None)

    @property
    def loaded(self) -> tuple[str, ...]:
        return tuple(m.id for m in self.models if m.loaded)


@dataclass(frozen=True)
class SystemSnapshot:
    taken_at: float
    platform: str
    cpu_count: int
    load_avg: tuple[float, float, float] | None
    memory: MemoryState
    swap: SwapState = field(default_factory=SwapState)
    thermal: ThermalState = field(default_factory=ThermalState)
    processes: tuple[ProcessUsage, ...] = ()
    llm: LlmServerState | None = None
    probe_errors: tuple[str, ...] = ()  # e.g. "pmset: not found"; a missing reading is never a failure

    @property
    def load_per_cpu(self) -> float | None:
        if not self.load_avg or not self.cpu_count:
            return None
        return round(self.load_avg[0] / self.cpu_count, 2)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["memory"]["available_pct"] = self.memory.available_pct
        out["swap"]["used_pct"] = self.swap.used_pct
        out["thermal"]["throttled"] = self.thermal.throttled
        out["load_per_cpu"] = self.load_per_cpu
        return out


@dataclass(frozen=True)
class Reason:
    code: str  # stable id, e.g. memory_pressure, swap_high, llm_unreachable
    severity: str  # info | reduce | minimal | block
    scope: str  # system | llm
    message: str


@dataclass(frozen=True)
class PreflightDecision:
    status: Status
    concurrency: int  # 0 when blocked
    timeout_multiplier: float
    reasons: tuple[Reason, ...] = ()

    @property
    def blocked(self) -> bool:
        return self.status is Status.BLOCKED

    def blocking(self, scope: str | None = None) -> tuple[Reason, ...]:
        return tuple(r for r in self.reasons if r.severity == "block" and scope in (None, r.scope))

    def summary(self) -> str:
        head = f"{self.status.value}: concurrency {self.concurrency}, timeouts x{self.timeout_multiplier:g}"
        return head + "".join(f"; {r.message}" for r in self.reasons if r.severity != "info")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "concurrency": self.concurrency,
            "timeout_multiplier": self.timeout_multiplier,
            "reasons": [asdict(r) for r in self.reasons],
        }


_GiB = 1024**3


def _rank(level: str | None, order: Sequence[str]) -> int:
    return order.index(level) if level in order else -1


def decide(
    snapshot: SystemSnapshot,
    settings: SystemPreflight | None = None,
    *,
    required_models: Iterable[str] = (),
    max_concurrency: int | None = None,
    own_requests: int = 0,
) -> PreflightDecision:
    """The preflight policy. ``max_concurrency`` (default ``settings.max_concurrency``) is the ceiling when
    all is well; ``own_requests`` are requests this process has in flight (a sampler during a run), so only
    requests queued *beyond* them count as other clients."""
    s = settings or SystemPreflight()
    ceiling = max(1, max_concurrency if max_concurrency is not None else s.max_concurrency)
    reasons: list[Reason] = []

    def add(code: str, severity: str, message: str, scope: str = "system") -> None:
        reasons.append(Reason(code, severity, scope, message))

    # memory ------------------------------------------------------------------------------------------
    mem = snapshot.memory
    pressure = _rank(mem.pressure, PRESSURE_LEVELS)
    if s.memory_pressure_block and pressure >= _rank(s.memory_pressure_block, PRESSURE_LEVELS):
        add("memory_pressure", "block", f"memory pressure is {mem.pressure}")
    elif pressure >= _rank(s.memory_pressure_reduce, PRESSURE_LEVELS):
        add("memory_pressure", "reduce", f"memory pressure is {mem.pressure}")
    avail = mem.available_pct
    if avail is not None:
        if avail < s.available_memory_block_pct:
            add("memory_available", "block", f"only {avail:g}% memory available")
        elif avail < s.available_memory_reduce_pct:
            add("memory_available", "reduce", f"only {avail:g}% memory available")
    swap = snapshot.swap.used_pct
    under_pressure = pressure >= _rank(s.memory_pressure_reduce, PRESSURE_LEVELS)
    # macOS grows and shrinks its swap file, so the percentage alone misleads: 6.9 of 8.0 GiB with pressure
    # normal is a small swap file, no stall; the percentage counts from swap_min_gib used or under pressure.
    if swap is not None and (under_pressure or snapshot.swap.used_bytes >= s.swap_min_gib * _GiB):
        used = (
            f"swap {snapshot.swap.used_bytes / _GiB:.1f} of {snapshot.swap.total_bytes / _GiB:.1f} GiB used"
        )
        if swap >= s.swap_minimal_pct:
            add("swap_full", "minimal", f"{used} ({swap:g}%)")
        elif swap >= s.swap_reduce_pct:
            # a nearly full swap *and* memory pressure: the next model allocation pages out the other model
            add("swap_high", "minimal" if under_pressure else "reduce", f"{used} ({swap:g}%)")

    # thermal ------------------------------------------------------------------------------------------
    th = snapshot.thermal
    level = _rank(th.level, THERMAL_LEVELS)
    if s.thermal_block and level >= _rank(s.thermal_block, THERMAL_LEVELS):
        add("thermal", "block", f"thermal state {th.level}")
    elif level >= _rank(s.thermal_minimal, THERMAL_LEVELS):
        add("thermal", "minimal", f"thermal state {th.level}")
    elif level >= _rank(s.thermal_reduce, THERMAL_LEVELS) and level > 0:
        add("thermal", "reduce", f"thermal state {th.level}")
    if th.throttled:
        add("cpu_throttled", "minimal", f"CPU speed limited to {th.cpu_speed_limit}%")
    elif th.warning:
        add("thermal_warning", "reduce", "a thermal warning level is recorded")

    # load ---------------------------------------------------------------------------------------------
    lpc = snapshot.load_per_cpu
    if lpc is not None:
        if lpc >= s.load_per_cpu_minimal:
            add("load", "minimal", f"load {lpc:g} per CPU")
        elif lpc >= s.load_per_cpu_reduce:
            add("load", "reduce", f"load {lpc:g} per CPU")

    # LLM server ---------------------------------------------------------------------------------------
    required = tuple(dict.fromkeys(required_models))
    llm = snapshot.llm
    if llm is not None and required:
        if not llm.reachable:
            add("llm_unreachable", "block", f"LLM server not reachable at {llm.base_url}", "llm")
        else:
            parallel: list[int] = []
            for model_id in required:
                m = llm.model(model_id)
                if m is None:
                    add("model_missing", "block", f"model '{model_id}' is not served", "llm")
                    continue
                if m.loaded is False and s.require_loaded_models:
                    add("model_not_loaded", "block", f"model '{model_id}' is not loaded", "llm")
                    continue
                if m.parallel:
                    parallel.append(m.parallel)
                others = (m.queued or 0) - own_requests
                if others >= s.llm_busy_queued:
                    add(
                        "llm_busy",
                        "reduce",
                        f"'{model_id}' has {others} request(s) queued by other clients",
                        "llm",
                    )
            if parallel and min(parallel) < ceiling:
                add("llm_parallel", "info", f"the server runs {min(parallel)} parallel prediction(s)", "llm")
                ceiling = min(parallel)

    # combine ------------------------------------------------------------------------------------------
    severities = {r.severity for r in reasons}
    if "block" in severities:
        return PreflightDecision(Status.BLOCKED, 0, s.minimal_timeout_multiplier, tuple(reasons))
    if "minimal" in severities:
        return PreflightDecision(Status.DEGRADED, 1, s.minimal_timeout_multiplier, tuple(reasons))
    if "reduce" in severities:
        concurrency = min(ceiling, s.reduced_concurrency)
        return PreflightDecision(Status.DEGRADED, concurrency, s.reduced_timeout_multiplier, tuple(reasons))
    return PreflightDecision(Status.OK, ceiling, 1.0, tuple(reasons))
