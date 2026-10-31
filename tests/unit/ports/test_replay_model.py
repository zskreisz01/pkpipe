"""Spec 002 T005 / feeds spec 013: recorded real-model responses replay deterministically."""

from __future__ import annotations

import jsonschema
import pytest

from pkpipe.ports import ModelError, ModelPort
from tests.fakes.replay import ReplayModel, load_recordings


def test_recordings_exist_and_are_internally_consistent():
    recs = load_recordings()
    assert len(recs) >= 3
    for rec in recs:
        jsonschema.validate(rec["data"], rec["request"]["json_schema"])
        assert rec["model"] and rec["raw_content"]


def test_replay_returns_recorded_data_for_matching_request_and_is_a_port():
    rec = load_recordings()[0]
    model = ReplayModel()
    assert isinstance(model, ModelPort)
    req = rec["request"]
    out = model.complete(req["system"], req["user"], req["json_schema"])
    assert out == rec["data"]
    out["mutated"] = True  # callers cannot corrupt the recording
    assert "mutated" not in model.complete(req["system"], req["user"], req["json_schema"])


def test_replay_unknown_request_fails_loudly():
    with pytest.raises(ModelError, match="no recording"):
        ReplayModel().complete("s", "never recorded", {"type": "object"})
