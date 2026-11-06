"""One preflight: snapshot -> decision, recorded as a ``system.preflight`` span and ``pkpipe.system.*``
gauges."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pkpipe.domain.system import (
    PRESSURE_LEVELS,
    THERMAL_LEVELS,
    PreflightDecision,
    Status,
    SystemSnapshot,
    decide,
)
from pkpipe.engine.config_models import SystemPreflight
from pkpipe.ports import SystemProbePort
from pkpipe.telemetry import Telemetry, attrs, get_telemetry

_DECISION_VALUE = {Status.OK: 0, Status.DEGRADED: 1, Status.BLOCKED: 2}
_MiB = 1024**2


@dataclass(frozen=True)
class Preflight:
    snapshot: SystemSnapshot
    decision: PreflightDecision
    required_models: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot": self.snapshot.to_dict(),
            "decision": self.decision.to_dict(),
            "required_models": list(self.required_models),
        }


def model_targets(
    snapshot: Any,
    environ: Mapping[str, str] | None = None,
    *,
    judge: str = "on",
    vision: bool = False,
) -> tuple[str | None, tuple[str, ...]]:
    """The LLM server URL (``default`` profile) and the models a run needs on it: the model under test and,
    when it is served by the same server, the eval judge (``evals.judge_profile``). ``judge="off"`` needs no
    judge, ``judge="only"`` only the judge (the subject answers from a cassette, spec 021); ``vision`` adds
    the model of the ``vision`` profile (the ``images`` suite)."""
    from pkpipe.adapters.model_openai import ProfileError, resolve_profile
    from pkpipe.engine.config_models import EvalsConfig, ModelsConfig

    env = os.environ if environ is None else environ
    models = ModelsConfig.model_validate(snapshot.data.get("models") or {})
    judge_profile = EvalsConfig.model_validate(snapshot.data.get("evals") or {}).judge_profile
    try:
        default = resolve_profile("default", models, env)
    except ProfileError:
        return None, ()
    names = [] if judge == "only" else [default.model]
    if vision:
        try:
            v = resolve_profile("vision", models, env)
            if v.base_url == default.base_url and judge != "only":
                names.append(v.model)
        except ProfileError:
            pass
    if judge != "off":
        try:
            j = resolve_profile(judge_profile, models, env)
            if j.base_url == default.base_url:
                names.append(j.model)
        except ProfileError:
            pass
    return default.base_url, tuple(dict.fromkeys(names))


def preflight_settings(snapshot: Any) -> SystemPreflight:
    from pkpipe.engine.config_models import EvalsConfig

    return EvalsConfig.model_validate(snapshot.data.get("evals") or {}).preflight


def span_attributes(snapshot: SystemSnapshot, decision: PreflightDecision) -> dict[str, Any]:
    """Flat OTel attributes of a snapshot and its decision. Process entries are ``name=MiB`` (names only)."""
    s, m = snapshot, snapshot.memory
    out: dict[str, Any] = {
        attrs.SPAN_KIND: "system",
        "system.platform": s.platform,
        "system.cpu.count": s.cpu_count,
        "system.memory.total_bytes": m.total_bytes,
        "system.swap.total_bytes": s.swap.total_bytes,
        "system.swap.used_bytes": s.swap.used_bytes,
        "system.thermal.throttled": s.thermal.throttled,
        "system.thermal.warning": s.thermal.warning,
        "system.top_processes": [f"{p.name}={p.rss_bytes // _MiB}" for p in s.processes],
        "system.probe_errors": list(s.probe_errors),
        "system.decision": decision.status.value,
        "system.eval_concurrency": decision.concurrency,
        "system.timeout_multiplier": decision.timeout_multiplier,
        "system.reasons": [f"{r.severity}:{r.code}" for r in decision.reasons],
    }
    optional = {
        "system.memory.available_bytes": m.available_bytes,
        "system.memory.available_pct": m.available_pct,
        "system.memory.free_bytes": m.free_bytes,
        "system.memory.compressed_bytes": m.compressed_bytes,
        "system.memory.pressure": m.pressure,
        "system.swap.used_pct": s.swap.used_pct,
        "system.thermal.state": s.thermal.level,
        "system.thermal.cpu_speed_limit": s.thermal.cpu_speed_limit,
        "system.load.1m": s.load_avg[0] if s.load_avg else None,
        "system.load.per_cpu": s.load_per_cpu,
    }
    out.update({k: v for k, v in optional.items() if v is not None})
    if s.llm is not None:
        out["llm.server.url"] = s.llm.base_url
        out["llm.server.reachable"] = s.llm.reachable
        out["llm.server.sources"] = list(s.llm.sources)
        out["llm.server.models_loaded"] = list(s.llm.loaded)
        out["llm.server.models"] = [
            f"{x.id}|parallel={x.parallel}|context={x.context_length}|status={x.status}|queued={x.queued}"
            for x in s.llm.models
            if x.loaded
        ]
    return out


def gauge_values(snapshot: SystemSnapshot, decision: PreflightDecision) -> dict[str, float | None]:
    m = snapshot.memory
    pressure = PRESSURE_LEVELS.index(m.pressure) if m.pressure in PRESSURE_LEVELS else -1
    thermal = THERMAL_LEVELS.index(snapshot.thermal.level) if snapshot.thermal.level in THERMAL_LEVELS else -1
    return {
        attrs.M_SYSTEM_MEMORY_PRESSURE: pressure,
        attrs.M_SYSTEM_MEMORY_AVAILABLE: (m.available_pct / 100) if m.available_pct is not None else None,
        attrs.M_SYSTEM_SWAP_USED: snapshot.swap.used_bytes,
        attrs.M_SYSTEM_THERMAL: thermal,
        attrs.M_SYSTEM_LOAD: snapshot.load_per_cpu,
        attrs.M_SYSTEM_DECISION: _DECISION_VALUE[decision.status],
        attrs.M_SYSTEM_CONCURRENCY: decision.concurrency,
    }


def record(
    snapshot: SystemSnapshot,
    decision: PreflightDecision,
    telemetry: Telemetry | None = None,
    *,
    span: bool = True,
    source: str = "preflight",
) -> None:
    """Gauges always; the ``system.preflight`` span when ``span`` (the sampler records gauges only, plus a
    span when the decision changes)."""
    tel = telemetry or get_telemetry()
    tel.metrics.system_sample(gauge_values(snapshot, decision), {"source": source})
    if span:
        with tel.tracer.start_as_current_span(attrs.SYSTEM_PREFLIGHT_SPAN) as sp:
            for key, value in span_attributes(snapshot, decision).items():
                sp.set_attribute(key, value)
            sp.set_attribute("system.source", source)


def run_preflight(
    probe: SystemProbePort,
    settings: SystemPreflight | None = None,
    *,
    required_models: Iterable[str] = (),
    max_concurrency: int | None = None,
    telemetry: Telemetry | None = None,
    include_llm: bool = True,
) -> Preflight:
    """Read one snapshot, decide, and record it (span + gauges)."""
    required = tuple(required_models)
    snapshot = probe.snapshot(include_llm=include_llm)
    decision = decide(snapshot, settings, required_models=required, max_concurrency=max_concurrency)
    record(snapshot, decision, telemetry)
    return Preflight(snapshot, decision, required)
