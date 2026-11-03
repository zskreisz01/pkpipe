"""System preflight telemetry and sampler (docs/operations.md "System preflight"): a ``system.preflight`` span
with the snapshot as attributes (process names only), ``pkpipe.system.*`` gauges, and a background sampler
thread whose decision follows the machine, captured with the in-memory OTel exporter and reader."""

from __future__ import annotations

import threading
import time

from pkpipe.domain.system import ProcessUsage, Status
from pkpipe.system import SystemSampler, run_preflight
from pkpipe.telemetry import attrs
from tests.fakes import FakeSystemProbe
from tests.fakes.system import JUDGE, MODEL, GiB, healthy_snapshot
from tests.support.otel import make_in_memory

REQUIRED = (MODEL, JUDGE)


def test_preflight_span_carries_the_snapshot_and_the_decision():
    o = make_in_memory()
    snap = healthy_snapshot(
        memory={"pressure": "warn"},
        processes=(ProcessUsage("llm-server", 12 * GiB), ProcessUsage("editor", GiB)),
    )
    pre = run_preflight(FakeSystemProbe(snap), required_models=REQUIRED, telemetry=o.telemetry)
    assert pre.decision.status is Status.DEGRADED
    [span] = o.spans("system.preflight")
    a = dict(span.attributes)
    assert a[attrs.SPAN_KIND] == "system" and a["system.source"] == "preflight"
    assert a["system.memory.pressure"] == "warn" and a["system.memory.total_bytes"] == 32 * GiB
    assert a["system.swap.used_bytes"] == GiB and a["system.thermal.state"] == "nominal"
    assert a["system.cpu.count"] == 10 and a["system.load.per_cpu"] == 0.2
    assert tuple(a["system.top_processes"]) == ("llm-server=12288", "editor=1024")
    assert a["system.decision"] == "degraded" and a["system.eval_concurrency"] == 2
    assert a["system.timeout_multiplier"] == 1.5 and tuple(a["system.reasons"]) == ("reduce:memory_pressure",)
    assert a["llm.server.reachable"] is True and set(a["llm.server.models_loaded"]) == set(REQUIRED)
    assert a["pkpipe.content_capture"] is False  # outside a flow: nothing private may be captured
    assert o.metric_value(attrs.M_SYSTEM_MEMORY_PRESSURE) == 1
    assert o.metric_value(attrs.M_SYSTEM_DECISION) == 1 and o.metric_value(attrs.M_SYSTEM_CONCURRENCY) == 2
    assert o.metric_value(attrs.M_SYSTEM_SWAP_USED, source="preflight") == GiB
    assert o.metric_value(attrs.M_SYSTEM_MEMORY_AVAILABLE) == 0.5
    o.telemetry.shutdown()


def test_span_attributes_never_include_paths_or_arguments():
    """Process names only: the probe already drops paths; the span shows nothing else of a process."""
    o = make_in_memory()
    snap = healthy_snapshot(processes=(ProcessUsage("tool", GiB),))
    run_preflight(FakeSystemProbe(snap), telemetry=o.telemetry)
    [span] = o.spans("system.preflight")
    flat = repr(dict(span.attributes))
    assert "/Users" not in flat and "--" not in flat
    o.telemetry.shutdown()


def test_sampler_lowers_concurrency_mid_run_and_summarizes():
    """A thermal / memory degradation in the middle of a run lowers the concurrency of later evals; every
    change is a span and an on_change call, every sample sets the gauges."""
    o = make_in_memory()
    clock = iter(1_800_000_000.0 + 60 * i for i in range(100))
    snaps = [
        healthy_snapshot(taken_at=1_800_000_000.0),
        healthy_snapshot(
            taken_at=1_800_000_060.0, swap={"total_bytes": 16 * GiB, "used_bytes": 14 * GiB}
        ),  # 87.5%
        healthy_snapshot(taken_at=1_800_000_120.0, thermal={"level": "serious", "cpu_speed_limit": 80}),
        healthy_snapshot(taken_at=1_800_000_180.0),
    ]
    probe = FakeSystemProbe(snaps)
    first = run_preflight(probe, required_models=REQUIRED, telemetry=o.telemetry)
    changes = []
    sampler = SystemSampler(
        probe,
        required_models=REQUIRED,
        telemetry=o.telemetry,
        on_change=changes.append,
        initial=first,
        clock=lambda: next(clock),
    )
    assert sampler.decision.concurrency == 4
    assert [sampler.sample_once().concurrency for _ in range(3)] == [2, 1, 4]
    assert [d.concurrency for d in changes] == [2, 1, 4]
    summary = sampler.stop()
    assert summary.samples == 4 and summary.swap_min == GiB and summary.swap_max == 14 * GiB
    assert summary.thermal_events == ["+2.0 min serious, CPU speed 80%", "+3.0 min nominal"]
    assert [c.split(" (")[0] for c in summary.concurrency_changes] == [
        "+1.0 min 4 -> 2",
        "+2.0 min 2 -> 1",
        "+3.0 min 1 -> 4",
    ]
    text = "\n".join(summary.lines())
    assert "swap used min 1.00 / max 14.00 GiB" in text and "thermal events: +2.0 min serious" in text
    sources = [s.attributes["system.source"] for s in o.spans("system.preflight")]
    assert sources == ["preflight", "sampler", "sampler", "sampler"]
    o.telemetry.shutdown()


def test_sampler_thread_starts_samples_periodically_and_stops():
    o = make_in_memory()
    probe = FakeSystemProbe(healthy_snapshot())
    seen = threading.Event()
    sampler = SystemSampler(probe, interval_s=0.02, telemetry=o.telemetry, on_change=lambda d: seen.set())
    sampler.start()
    try:
        assert seen.wait(5) and sampler.running
        deadline = time.monotonic() + 5
        while len(probe.calls) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(probe.calls) >= 3
    finally:
        sampler.stop()
    assert not sampler.running
    calls = len(probe.calls)
    time.sleep(0.1)
    assert len(probe.calls) == calls  # stopped means stopped
    assert not any(t.name == "pkpipe-system-sampler" for t in threading.enumerate())
    o.telemetry.shutdown()


def test_a_failing_probe_keeps_the_last_decision():
    o = make_in_memory()

    class Flaky(FakeSystemProbe):
        fail = False

        def snapshot(self, *, include_llm=True):
            if self.fail:
                raise OSError("sysctl vanished")
            return super().snapshot(include_llm=include_llm)

    probe = Flaky(healthy_snapshot(memory={"pressure": "warn"}))
    sampler = SystemSampler(probe, telemetry=o.telemetry)
    assert sampler.sample_once().concurrency == 2
    probe.fail = True
    assert sampler.sample_once().concurrency == 2 and sampler.summary.errors == 1
    o.telemetry.shutdown()
