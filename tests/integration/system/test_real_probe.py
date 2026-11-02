"""The real system probe on this machine (docs/operations.md "System preflight"): sane readings without sudo,
fast enough to sample during a live run. The live test also reads the real LM Studio (app or llmster daemon)
without loading or unloading anything."""

from __future__ import annotations

import sys
import time

import pytest

from pkpipe.adapters.system_probe import make_system_probe
from pkpipe.domain.system import PRESSURE_LEVELS, THERMAL_LEVELS, decide
from tests.support import live


def test_real_probe_reads_sane_host_values_quickly():
    probe = make_system_probe(None)
    start = time.monotonic()
    snap = probe.snapshot(include_llm=False)
    elapsed = time.monotonic() - start
    assert elapsed < 5, f"a snapshot must be cheap (took {elapsed:.2f} s)"
    assert snap.llm is None and snap.cpu_count >= 1
    assert snap.memory.total_bytes >= 1024**3
    assert snap.memory.available_bytes is None or 0 < snap.memory.available_bytes <= snap.memory.total_bytes
    assert 0 <= snap.swap.used_bytes <= max(snap.swap.total_bytes, 0) or snap.swap.total_bytes == 0
    assert snap.load_avg is not None and all(x >= 0 for x in snap.load_avg)
    assert snap.processes and all("/" not in p.name and p.rss_bytes > 0 for p in snap.processes)
    if sys.platform == "darwin":
        assert snap.probe_errors == (), snap.probe_errors
        assert snap.memory.pressure in PRESSURE_LEVELS and snap.thermal.level in THERMAL_LEVELS
        assert snap.memory.compressed_bytes is not None and snap.memory.free_bytes is not None
    decision = decide(snap)
    assert decision.status.value in ("ok", "degraded", "blocked") and decision.timeout_multiplier >= 1


@pytest.mark.live
def test_real_probe_reads_the_live_lm_studio_server():
    """Live: the model under test is served and (with LM Studio's REST API or lms) reported as loaded with its
    parallel predictions and context; nothing is loaded or unloaded."""
    snap = make_system_probe(live.model_base_url()).snapshot()
    assert snap.llm is not None and snap.llm.reachable, snap.llm
    model = snap.llm.model(live.model_name())
    assert model is not None, [m.id for m in snap.llm.models]
    if "lmstudio-rest" in snap.llm.sources or "lms" in snap.llm.sources:
        assert model.loaded is True
    if "lms" in snap.llm.sources:
        assert model.parallel and model.parallel >= 1 and model.context_length
    decision = decide(snap, required_models=(live.model_name(),), max_concurrency=live.LIVE_EVAL_CONCURRENCY)
    assert not decision.blocking("llm"), decision.summary()
