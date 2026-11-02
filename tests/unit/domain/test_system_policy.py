"""System preflight policy (docs/operations.md "System preflight"): a snapshot and the ``evals.preflight``
thresholds -> ok / degraded / blocked, eval concurrency, timeout multiplier and reasons. Code decides
(constitution V); table-driven over one healthy machine with one thing changed per row."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pkpipe.domain.system import LlmModel, LlmServerState, Status, decide
from pkpipe.engine.config_models import EvalsConfig, SystemPreflight
from tests.fakes.system import JUDGE, MODEL, GiB, healthy_snapshot

REQUIRED = (MODEL, JUDGE)


def _llm(**model_changes) -> dict:
    models = tuple(
        LlmModel(m, loaded=True, parallel=4, context_length=32768, status="idle", queued=0) for m in REQUIRED
    )
    first = LlmModel(**{**models[0].__dict__, **model_changes})
    return {"models": (first, models[1])}


CASES = [
    # id, snapshot changes, expected status, concurrency, timeout multiplier, reason codes (severity:code)
    ("healthy", {}, Status.OK, 4, 1.0, set()),
    ("pressure-warn", {"memory": {"pressure": "warn"}}, Status.DEGRADED, 2, 1.5, {"reduce:memory_pressure"}),
    (
        "pressure-critical",
        {"memory": {"pressure": "critical"}},
        Status.BLOCKED,
        0,
        2.0,
        {"block:memory_pressure"},
    ),
    (
        "available-low",
        {"memory": {"available_bytes": int(2.5 * GiB)}},
        Status.DEGRADED,
        2,
        1.5,
        {"reduce:memory_available"},
    ),
    (
        "available-gone",
        {"memory": {"available_bytes": GiB // 2}},
        Status.BLOCKED,
        0,
        2.0,
        {"block:memory_available"},
    ),
    (
        "swap-high",
        {"swap": {"total_bytes": 20 * GiB, "used_bytes": 17 * GiB}},
        Status.DEGRADED,
        2,
        1.5,
        {"reduce:swap_high"},
    ),
    (
        "swap-high-under-pressure",
        {"swap": {"total_bytes": 20 * GiB, "used_bytes": 17 * GiB}, "memory": {"pressure": "warn"}},
        Status.DEGRADED,
        1,
        2.0,
        {"reduce:memory_pressure", "minimal:swap_high"},
    ),
    (
        "swap-full",
        {"swap": {"total_bytes": 20 * GiB, "used_bytes": 19.5 * GiB}},
        Status.DEGRADED,
        1,
        2.0,
        {"minimal:swap_full"},
    ),
    ("thermal-fair", {"thermal": {"level": "fair"}}, Status.DEGRADED, 2, 1.5, {"reduce:thermal"}),
    ("thermal-serious", {"thermal": {"level": "serious"}}, Status.DEGRADED, 1, 2.0, {"minimal:thermal"}),
    ("thermal-critical", {"thermal": {"level": "critical"}}, Status.BLOCKED, 0, 2.0, {"block:thermal"}),
    (
        "cpu-throttled",
        {"thermal": {"cpu_speed_limit": 70}},
        Status.DEGRADED,
        1,
        2.0,
        {"minimal:cpu_throttled"},
    ),
    ("thermal-warning", {"thermal": {"warning": True}}, Status.DEGRADED, 2, 1.5, {"reduce:thermal_warning"}),
    ("load-high", {"load_avg": (16.0, 9.0, 5.0)}, Status.DEGRADED, 2, 1.5, {"reduce:load"}),
    ("load-extreme", {"load_avg": (31.0, 20.0, 9.0)}, Status.DEGRADED, 1, 2.0, {"minimal:load"}),
    (
        "llm-unreachable",
        {"llm": {"reachable": False, "models": (), "error": "URLError"}},
        Status.BLOCKED,
        0,
        2.0,
        {"block:llm_unreachable"},
    ),
    ("model-missing", {"llm": {"models": ()}}, Status.BLOCKED, 0, 2.0, {"block:model_missing"}),
    ("model-not-loaded", {"llm": _llm(loaded=False)}, Status.BLOCKED, 0, 2.0, {"block:model_not_loaded"}),
    ("llm-busy", {"llm": _llm(queued=3, status="generating")}, Status.DEGRADED, 2, 1.5, {"reduce:llm_busy"}),
    ("llm-parallel-1", {"llm": _llm(parallel=1)}, Status.OK, 1, 1.0, {"info:llm_parallel"}),
    (
        "unknown-readings",
        {
            "memory": {"pressure": None, "available_bytes": None},
            "load_avg": None,
            "thermal": {"level": None},
            "swap": {"total_bytes": 0, "used_bytes": 0},
        },
        Status.OK,
        4,
        1.0,
        set(),
    ),
    # the reference Mac mini during a slow live run: swap 17.55 of 18.43 GB, pressure warn, a second client
    (
        "reference-slow-run",
        {
            "swap": {"total_bytes": int(18.43 * 1e9), "used_bytes": int(17.55 * 1e9)},
            "memory": {"pressure": "warn"},
            "llm": _llm(queued=2, status="generating"),
        },
        Status.DEGRADED,
        1,
        2.0,
        {"reduce:memory_pressure", "minimal:swap_full", "reduce:llm_busy"},
    ),
]


@pytest.mark.parametrize(
    ("changes", "status", "concurrency", "multiplier", "codes"),
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_policy_table(changes, status, concurrency, multiplier, codes):
    decision = decide(healthy_snapshot(**changes), required_models=REQUIRED)
    assert decision.status is status
    assert decision.concurrency == concurrency
    assert decision.timeout_multiplier == multiplier
    assert {f"{r.severity}:{r.code}" for r in decision.reasons} == codes
    assert decision.blocked == (status is Status.BLOCKED)


def test_blocked_reasons_name_their_scope_so_callers_can_leave_llm_blocks_to_service_probes():
    system = decide(healthy_snapshot(thermal={"level": "critical"}), required_models=REQUIRED)
    llm = decide(healthy_snapshot(llm={"reachable": False, "models": ()}), required_models=REQUIRED)
    assert [r.code for r in system.blocking("system")] == ["thermal"] and not system.blocking("llm")
    assert [r.code for r in llm.blocking("llm")] == ["llm_unreachable"] and not llm.blocking("system")
    assert "LLM server not reachable at http://127.0.0.1:1234/v1" in llm.summary()


def test_the_ceiling_comes_from_the_caller_and_degradation_never_raises_it():
    """Production `pkpipe eval` passes evals.concurrency (1): the policy can only lower it."""
    assert decide(healthy_snapshot(), required_models=REQUIRED, max_concurrency=1).concurrency == 1
    assert decide(healthy_snapshot(memory={"pressure": "warn"}), max_concurrency=1).concurrency == 1
    assert decide(healthy_snapshot(), max_concurrency=8).concurrency == 8


def test_own_requests_are_not_other_clients():
    """The sampler runs while our own evals are in flight: only requests beyond them count as busy."""
    snap = healthy_snapshot(llm=_llm(queued=4, status="generating"))
    assert decide(snap, required_models=REQUIRED, own_requests=4).status is Status.OK
    assert decide(snap, required_models=REQUIRED, own_requests=2).status is Status.DEGRADED


def test_without_required_models_the_llm_server_is_not_judged():
    snap = healthy_snapshot(llm={"reachable": False, "models": ()})
    assert decide(snap).status is Status.OK


def test_thresholds_come_from_config():
    strict = SystemPreflight(
        swap_reduce_pct=5, swap_minimal_pct=6, thermal_block="fair", reduced_concurrency=3
    )
    assert decide(healthy_snapshot(), strict).status is Status.DEGRADED  # 12.5% swap >= 6%
    assert decide(healthy_snapshot(thermal={"level": "fair"}), strict).status is Status.BLOCKED
    lenient = SystemPreflight(memory_pressure_block=None)
    assert decide(healthy_snapshot(memory={"pressure": "critical"}), lenient).concurrency == 2
    relaxed = SystemPreflight(require_loaded_models=False)
    assert (
        decide(healthy_snapshot(llm=_llm(loaded=False)), relaxed, required_models=REQUIRED).status
        is Status.OK
    )


def test_config_rejects_inverted_thresholds_and_evals_json_carries_the_defaults():
    with pytest.raises(ValidationError, match="swap_minimal_pct"):
        SystemPreflight(swap_reduce_pct=90, swap_minimal_pct=80)
    with pytest.raises(ValidationError, match="available_memory_block_pct"):
        SystemPreflight(available_memory_reduce_pct=5, available_memory_block_pct=10)
    with pytest.raises(ValidationError):
        EvalsConfig.model_validate({"preflight": {"surprise": 1}})
    assert EvalsConfig().preflight == SystemPreflight()
    assert EvalsConfig.model_validate({"preflight": {"max_concurrency": 2}}).preflight.max_concurrency == 2


def test_decision_and_snapshot_serialize():
    snap = healthy_snapshot(memory={"pressure": "warn"})
    decision = decide(snap)
    data = snap.to_dict()
    assert data["memory"]["available_pct"] == 50.0 and data["swap"]["used_pct"] == 12.5
    assert data["load_per_cpu"] == 0.2 and data["processes"][0] == {
        "name": "llm-server",
        "rss_bytes": 12 * GiB,
    }
    assert decision.to_dict()["reasons"][0]["code"] == "memory_pressure"
    assert LlmServerState("u", True).loaded == ()
