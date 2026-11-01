"""Spec 013 US3 / T003: cassette recorder and replayer for ModelPort."""

from __future__ import annotations

import json

import pytest

from pkpipe.evals.cassette import CassetteMiss, CassetteModel, request_key
from pkpipe.ports import ModelError, ModelPort, ModelUnavailable, SchemaError
from pkpipe.steps.base import datamark, datamark_notice, datamark_token
from tests.fakes import FakeModel

SCHEMA = {"type": "object", "properties": {"category": {"type": "string"}}}


def _prompt(text: str) -> tuple[str, str]:
    """A datamarked prompt like the llm step builds it: a fresh random token on every call."""
    token = datamark_token(text)
    return "You sort things.\n\n" + datamark_notice(token), "## Inputs\n\n" + datamark(text, token)


def test_record_then_replay_without_the_model(tmp_path):
    """US3: recorded responses answer later calls with no model at all, despite new datamark tokens."""
    path = tmp_path / "triage.json"
    inner = FakeModel([{"category": "tool"}])
    rec = CassetteModel(path, "record", inner, identities={"default": "http://x|m"})
    assert isinstance(rec, ModelPort)
    system, user = _prompt("a CLI tool")
    assert rec.complete(system, user, SCHEMA) == {"category": "tool"}
    assert rec.save() and len(inner.calls) == 1

    replay = CassetteModel(path, "replay")
    system2, user2 = _prompt("a CLI tool")
    assert user2 != user  # different random token ...
    assert replay.complete(system2, user2, SCHEMA) == {"category": "tool"}  # ... same recording
    assert replay.hits == 1
    stored = json.loads(path.read_text())
    assert stored["format"] == 1 and stored["entries"][0]["model"] == "http://x|m"
    assert "TOKEN" in stored["entries"][0]["request"]["user"]


def test_replay_miss_is_an_error_not_a_pass(tmp_path):
    """A changed prompt, schema or profile is visible: no silent fallback."""
    path = tmp_path / "c.json"
    rec = CassetteModel(path, "record", FakeModel([{"category": "tool"}]))
    rec.complete("s", "u", SCHEMA)
    rec.save()
    replay = CassetteModel(path, "replay")
    for args in (("s", "changed", SCHEMA), ("s", "u", None), ("s", "u", SCHEMA, "judge")):
        with pytest.raises(CassetteMiss):
            replay.complete(*args)
    assert issubclass(CassetteMiss, ModelError) and replay.misses == 3


def test_model_errors_are_recorded_and_replayed_as_the_same_type(tmp_path):
    path = tmp_path / "c.json"
    rec = CassetteModel(path, "record", FakeModel([SchemaError("bad json")]))
    with pytest.raises(SchemaError):
        rec.complete("s", "u", SCHEMA)
    rec.save()
    with pytest.raises(SchemaError, match="bad json"):
        CassetteModel(path, "replay").complete("s", "u", SCHEMA)


def test_an_unreachable_server_is_not_recorded(tmp_path):
    rec = CassetteModel(tmp_path / "c.json", "record", FakeModel([ModelUnavailable("down")]))
    with pytest.raises(ModelUnavailable):
        rec.complete("s", "u")
    assert len(rec) == 0 and not rec.save()


def test_auto_replays_known_and_records_new(tmp_path):
    path = tmp_path / "c.json"
    first = CassetteModel(path, "record", FakeModel([{"a": 1}]))
    first.complete("s", "one")
    first.save()
    inner = FakeModel([{"a": 2}])
    auto = CassetteModel(path, "auto", inner)
    assert auto.complete("s", "one") == {"a": 1} and auto.complete("s", "two") == {"a": 2}
    assert len(inner.calls) == 1 and auto.recorded == 1


def test_full_rerecord_prunes_stale_entries(tmp_path):
    path = tmp_path / "c.json"
    rec = CassetteModel(path, "record", FakeModel([{"a": 1}, {"a": 2}]))
    rec.complete("s", "old")
    rec.complete("s", "new")
    rec.save()
    again = CassetteModel(path, "record", FakeModel([{"a": 3}]))
    again.complete("s", "new")
    again.save(prune_to=again.used)
    assert CassetteModel(path, "replay").keys() == {request_key("s", "new", None, "default")}


def test_replies_are_copies(tmp_path):
    rec = CassetteModel(tmp_path / "c.json", "record", FakeModel([{"tags": ["a"]}]))
    rec.complete("s", "u")["tags"].append("mutated")
    assert rec.complete.__self__._entries[request_key("s", "u", None, "default")]["response"] == {
        "tags": ["a"]
    }


def test_modes_need_a_model_to_record(tmp_path):
    with pytest.raises(ValueError):
        CassetteModel(tmp_path / "c.json", "record")
    with pytest.raises(ValueError):
        CassetteModel(tmp_path / "c.json", "bogus")  # type: ignore[arg-type]


def test_unknown_cassette_format_is_rejected(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"format": 99, "entries": []}))
    with pytest.raises(ValueError, match="format"):
        CassetteModel(p, "replay")
