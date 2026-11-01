"""Spec 002 FR-003 / T002: models.json validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pkpipe.engine.config_models import ModelsConfig
from pkpipe.engine.registry import load_config


def test_profile_defaults_cover_every_fr003_field():
    p = ModelsConfig.model_validate({"default": {"base_url": "http://h/v1", "model": "m"}}).root["default"]
    assert (p.temperature, p.timeout_s, p.schema_retries) == (0.2, 120, 1)
    assert p.max_tokens is None and p.context is None and p.fallback is None


def test_all_fields_accepted():
    ModelsConfig.model_validate(
        {
            "default": {
                "base_url": "env:B",
                "model": "m",
                "temperature": 0.5,
                "max_tokens": 100,
                "context": 4096,
                "timeout_s": 30,
                "schema_retries": 2,
                "fallback": "small",
            },
            "small": {"base_url": "http://h/v1", "model": "s"},
        }
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"temperature": 3},
        {"max_tokens": 0},
        {"timeout_s": 0},
        {"schema_retries": -1},
        {"context": -5},
        {"unknown_key": 1},
        {"fallback": "ghost"},
    ],
)
def test_invalid_profile_rejected(bad):
    with pytest.raises(ValidationError):
        ModelsConfig.model_validate({"default": {"base_url": "u", "model": "m", **bad}})


def test_fallback_cycle_rejected():
    with pytest.raises(ValidationError, match="cycle"):
        ModelsConfig.model_validate(
            {
                "a": {"base_url": "u", "model": "m", "fallback": "b"},
                "b": {"base_url": "u", "model": "m", "fallback": "a"},
            }
        )


def test_public_pack_profiles_validate_and_resolve_without_any_env(pack_dir):
    from pkpipe.adapters.model_openai import resolve_profile

    models = ModelsConfig.model_validate(load_config(pack_dir)["models"])
    d = resolve_profile("default", models, {})
    j = resolve_profile("judge", models, {})
    assert d.base_url == "http://127.0.0.1:1234/v1" and d.model == "qwen3.5-9b-mlx"
    # spec 013 FR-002: the judge is a different model than the one under test, out of the box
    assert j.model == "google/gemma-4-12b" and j.model != d.model and j.temperature == 0.0
    env = {"PKPIPE_MODEL_BASE_URL": "http://localhost:11434/v1", "PKPIPE_JUDGE_MODEL": "judge-x"}
    assert resolve_profile("default", models, env).base_url == "http://localhost:11434/v1"
    assert resolve_profile("judge", models, env).model == "judge-x"
