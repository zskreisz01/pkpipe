"""Spec 002 T007 / SC-001: structured completions against the real LM Studio model (``-m live``)."""

from __future__ import annotations

import jsonschema
import pytest

from pkpipe.adapters.model_openai import OpenAIModel
from tests.fakes.replay import load_recordings

pytestmark = pytest.mark.live


@pytest.fixture
def model(lmstudio):
    profile = {
        "base_url": lmstudio["base_url"],
        "model": lmstudio["model"],
        "temperature": 0.0,
        "max_tokens": 2048,
        "timeout_s": 300,
    }
    return OpenAIModel({"default": profile, "judge": {**profile, "temperature": 0.0}}, environ={})


@pytest.mark.parametrize("rec", load_recordings(), ids=lambda r: r["request"]["user"][:30])
def test_every_recorded_request_gets_schema_valid_output_from_the_real_model(model, rec):
    """US1.1 / SC-001: output validates against the schema; categories stay inside the enum."""
    req = rec["request"]
    out = model.complete(req["system"], req["user"], req["json_schema"])
    jsonschema.validate(out, req["json_schema"])
    assert model.last.prompt_tokens and model.last.completion_tokens and model.last.latency_s > 0


def test_triage_classification_is_sensible(model):
    schema = {
        "type": "object",
        "properties": {"category": {"enum": ["tool", "recipe"]}},
        "required": ["category"],
        "additionalProperties": False,
    }
    out = model.complete(
        "Classify the note. JSON only.",
        "Note: synthetic CLI utility 'fooctl' that formats YAML.",
        schema,
        profile="judge",
    )
    assert out["category"] == "tool"


def test_plain_text_completion_has_no_think_block(model):
    out = model.complete("Answer in one word.", "Reply with exactly: pong")
    assert "<think>" not in out["text"] and "pong" in out["text"].lower()


def test_dead_primary_falls_back_to_the_real_model(lmstudio):
    """US2.2 live: primary endpoint down (nothing listens) -> LM Studio fallback answers valid JSON."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = f"http://127.0.0.1:{s.getsockname()[1]}/v1"
    models = {
        "default": {"base_url": dead, "model": "nope", "fallback": "local", "timeout_s": 5},
        "local": {"base_url": lmstudio["base_url"], "model": lmstudio["model"], "max_tokens": 2048},
    }
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "integer"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    model = OpenAIModel(models, environ={})
    out = model.complete("Reply with JSON only.", "What is 2 + 3? Put the number in 'answer'.", schema)
    assert out == {"answer": 5}
    assert model.last.used_fallback and model.last.profile == "local"
