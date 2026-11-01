"""Spec 013 SC-001 / FR-002: gate thresholds live in config (evals.json), the judge profile is validated, and
thresholds only move upwards (constitution IX)."""

from __future__ import annotations

import copy
import json

import pytest

from pkpipe.engine.config_models import EvalsConfig, config_json_schemas
from pkpipe.engine.registry import CORE_DEFAULTS, ConfigError, load_effective
from pkpipe.evals.gate import threshold_regressions


def test_pack_gates_are_in_config_and_exported_as_schema(pack_dir):
    """SC-001: thresholds are config, validated by the same models the UI uses."""
    cfg = load_effective(pack_dir, None)
    evals = EvalsConfig.model_validate(cfg.data["evals"])
    assert evals.suites["triage"].gate["accuracy"].min >= 0.8
    assert evals.suites["deterministic"].gate["pass_rate"].min == 1.0  # from the core defaults
    assert evals.judge_profile == "judge"
    assert "evals" in config_json_schemas()
    assert "evals" in CORE_DEFAULTS


def test_unknown_judge_profile_is_a_config_error(overlay_dir, write_json, pack_dir):
    write_json(overlay_dir, "evals.json", {"judge_profile": "nope"})
    with pytest.raises(ConfigError, match="judge_profile 'nope' is not a models.json profile"):
        load_effective(pack_dir, overlay_dir)


def test_judge_profile_used_by_an_llm_step_is_a_config_error(overlay_dir, write_json, pack_dir):
    """FR-002: the judge profile differs from the profile of every model under test."""
    write_json(overlay_dir, "evals.json", {"judge_profile": "default"})
    with pytest.raises(ConfigError, match="also used by an LLM step"):
        load_effective(pack_dir, overlay_dir)


def test_a_threshold_with_both_bounds_is_rejected(overlay_dir, write_json, pack_dir):
    write_json(
        overlay_dir, "evals.json", {"suites": {"triage": {"gate": {"accuracy": {"min": 1, "max": 2}}}}}
    )
    with pytest.raises(ConfigError, match="evals.json"):
        load_effective(pack_dir, overlay_dir)


def _evals(pack_dir) -> dict:
    return copy.deepcopy(load_effective(pack_dir, None).data["evals"])


def test_threshold_regressions(pack_dir):
    """Constitution IX: lowering a min, raising a max or dropping a gate is a regression; tightening is
    not."""
    old = _evals(pack_dir)
    new = copy.deepcopy(old)
    assert threshold_regressions(EvalsConfig(**old), EvalsConfig(**new)) == []
    new["suites"]["triage"]["gate"]["accuracy"]["min"] = 0.95  # stricter: fine
    new["suites"]["security"]["gate"]["false_positive_rate"]["max"] = 0.01
    assert threshold_regressions(EvalsConfig(**old), EvalsConfig(**new)) == []
    new["suites"]["triage"]["gate"]["accuracy"]["min"] = 0.5
    new["suites"]["security"]["gate"]["false_positive_rate"]["max"] = 0.5
    del new["suites"]["sessions"]["gate"]["judge_mean"]
    new["pairwise"]["min_score"] = 0.1
    found = threshold_regressions(EvalsConfig(**old), EvalsConfig(**new))
    assert any(f.startswith("triage.accuracy: min lowered") for f in found)
    assert any(f.startswith("security.false_positive_rate: max raised") for f in found)
    assert "sessions.judge_mean: gate removed" in found
    assert any(f.startswith("pairwise.min_score") for f in found)


def test_overlay_can_tighten_a_gate(overlay_dir, write_json, pack_dir):
    write_json(overlay_dir, "evals.json", {"suites": {"triage": {"gate": {"accuracy": {"min": 0.9}}}}})
    data = load_effective(pack_dir, overlay_dir).data["evals"]
    assert data["suites"]["triage"]["gate"]["accuracy"] == {"min": 0.9}
    assert "macro_f1" in data["suites"]["triage"]["gate"]  # merged key by key
    assert json.dumps(data)  # plain JSON
