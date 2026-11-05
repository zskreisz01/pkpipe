"""Spec 013 US3 / T003: cassette recorder and replayer for ModelPort."""

from __future__ import annotations

import json

import pytest

from pkpipe.evals.cassette import (
    CassetteMiss,
    CassetteModel,
    CassetteModelMismatch,
    MigrationError,
    cassette_key,
    migrate_v1,
    request_key,
)
from pkpipe.evals.migrate_cassettes import main as migrate_main
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

    replay = CassetteModel(path, "replay", identities={"default": "http://x|m"})
    system2, user2 = _prompt("a CLI tool")
    assert user2 != user  # different random token ...
    assert replay.complete(system2, user2, SCHEMA) == {"category": "tool"}  # ... same recording
    assert replay.hits == 1
    stored = json.loads(path.read_text())
    entry = stored["entries"][0]
    assert stored["format"] == 2 and entry["model"] == "m" and entry["server"] == "http://x"
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


@pytest.mark.parametrize(
    "error",
    [ModelUnavailable("down"), ModelError("HTTP 404 from http://h/v1: model 'm' is not served there")],
    ids=["unreachable", "unknown-model"],
)
def test_an_unreachable_server_or_refused_request_is_not_recorded(tmp_path, error):
    """Only the model's own failures are answers; an endpoint or profile problem must stay visible."""
    rec = CassetteModel(tmp_path / "c.json", "record", FakeModel([error]))
    with pytest.raises(type(error)):
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
    assert CassetteModel(path, "replay").keys() == {
        cassette_key(request_key("s", "new", None, "default"), None)
    }


def test_replies_are_copies(tmp_path):
    rec = CassetteModel(tmp_path / "c.json", "record", FakeModel([{"tags": ["a"]}]))
    rec.complete("s", "u")["tags"].append("mutated")
    key = cassette_key(request_key("s", "u", None, "default"), None)
    assert rec.complete.__self__._entries[key]["response"] == {"tags": ["a"]}


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


def test_run_canary_is_normalised_in_the_key():
    """Spec 009: a ``canary: true`` session carries the run's canary (random per run), so a replayed run must
    find the recording made in another run; other text differences still miss."""
    from pkpipe.evals.cassette import request_key

    a = request_key("sys\n\nInternal reference code: CNY-0123456789abcdef", "u", None, "default")
    b = request_key("sys\n\nInternal reference code: CNY-fedcba9876543210", "u", None, "default")
    c = request_key("sys\n\nInternal reference code: CNY-fedcba9876543210", "other", None, "default")
    assert a == b != c


# -- the model id is part of the key ----------------------------------------------------------------------


def _recorded(path, identity="http://127.0.0.1:1234/v1|model-a", reply=None):
    rec = CassetteModel(path, "record", FakeModel([reply or {"a": 1}]), identities={"default": identity})
    rec.complete("s", "u", SCHEMA)
    rec.save()


def test_a_model_switch_is_an_explicit_mismatch_in_replay(tmp_path):
    """A request recorded for model A is never answered for model B: CassetteModelMismatch (a CassetteMiss,
    so a suite errors) names both models and how to fix it."""
    path = tmp_path / "c.json"
    _recorded(path)
    replay = CassetteModel(path, "replay", identities={"default": "http://127.0.0.1:1234/v1|model-b"})
    with pytest.raises(CassetteModelMismatch, match="'model-a'.*'model-b'.*--cassette record"):
        replay.complete("s", "u", SCHEMA)
    assert issubclass(CassetteModelMismatch, CassetteMiss) and replay.misses == 1 and replay.hits == 0
    unknown = CassetteModel(path, "replay")  # the profile's model cannot be resolved
    with pytest.raises(CassetteModelMismatch, match="'model-a'.*None"):
        unknown.complete("s", "u", SCHEMA)


def test_a_new_request_is_a_plain_miss_not_a_mismatch(tmp_path):
    path = tmp_path / "c.json"
    _recorded(path)
    replay = CassetteModel(path, "replay", identities={"default": "http://h|model-a"})
    with pytest.raises(CassetteMiss) as exc:
        replay.complete("s", "changed", SCHEMA)
    assert type(exc.value) is CassetteMiss and "model 'model-a'" in str(exc.value)


def test_the_base_url_is_not_part_of_the_key(tmp_path):
    """The same model served elsewhere (CI's unreachable URL, another port or host) replays the recording."""
    path = tmp_path / "c.json"
    _recorded(path, reply={"a": 7})
    for identity in ("http://127.0.0.1:9/v1|model-a", "http://gpu-box:8001/v1|model-a", "model-a"):
        assert CassetteModel(path, "replay", identities={"default": identity}).complete("s", "u", SCHEMA) == {
            "a": 7
        }


def test_auto_records_the_new_model_next_to_the_old_one(tmp_path):
    path = tmp_path / "c.json"
    _recorded(path, reply={"a": 1})
    inner = FakeModel([{"a": 2}])
    auto = CassetteModel(path, "auto", inner, identities={"default": "http://h|model-b"})
    assert auto.complete("s", "u", SCHEMA) == {"a": 2} and len(inner.calls) == 1
    auto.save()
    for model, expected in (("model-a", {"a": 1}), ("model-b", {"a": 2})):
        replay = CassetteModel(path, "replay", identities={"default": f"http://h|{model}"})
        assert replay.complete("s", "u", SCHEMA) == expected


def test_a_format_1_cassette_is_refused_with_the_migration_command(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"format": 1, "entries": []}))
    with pytest.raises(ValueError, match="model id.*pkpipe.evals.migrate_cassettes"):
        CassetteModel(p, "replay")


# -- offline migration of format 1 cassettes --------------------------------------------------------------


def _v1_entry(user: str, identity: str | None, reply: dict, profile: str = "default") -> dict:
    """An entry as format 1 wrote it: key = request digest, ``model`` = the ``base_url|model`` identity."""
    return {
        "key": request_key("s <<<DATA TOKEN>>>", user, SCHEMA, profile),
        "profile": profile,
        "model": identity,
        "request": {"system": "s <<<DATA TOKEN>>>", "user": user, "json_schema": SCHEMA},
        "response": reply,
    }


def _v1(path, *entries) -> None:
    path.write_text(json.dumps({"format": 1, "entries": list(entries)}), encoding="utf-8")


def test_migration_rekeys_with_the_recorded_model_and_replays_the_same_answers(tmp_path):
    path = tmp_path / "c.json"
    _v1(
        path,
        _v1_entry("one", "http://127.0.0.1:1234/v1|model-a", {"a": 1}),
        _v1_entry("two", "http://127.0.0.1:1234/v1|judge-j", {"a": 2}, profile="judge"),
        {**_v1_entry("err", "http://h|model-a", {}), "response": None, "error": {"type": "SchemaError"}},
    )
    assert migrate_v1(path) == 3
    data = json.loads(path.read_text())
    assert data["format"] == 2 and {e["model"] for e in data["entries"]} == {"model-a", "judge-j"}
    assert {e["server"] for e in data["entries"]} == {"http://127.0.0.1:1234/v1", "http://h"}
    ids = {"default": "http://ci:9/v1|model-a", "judge": "http://ci:9/v1|judge-j"}
    replay = CassetteModel(path, "replay", identities=ids)
    assert replay.complete("s <<<DATA abcdef123456>>>", "one", SCHEMA) == {"a": 1}
    assert replay.complete("s <<<DATA 0123456789ab>>>", "two", SCHEMA, profile="judge") == {"a": 2}
    with pytest.raises(SchemaError):
        replay.complete("s <<<DATA 0123456789ab>>>", "err", SCHEMA)
    before = path.read_text()
    assert migrate_v1(path) is None and path.read_text() == before  # idempotent


def test_migration_refuses_a_key_that_does_not_verify_and_leaves_the_file(tmp_path):
    path = tmp_path / "c.json"
    tampered = {**_v1_entry("one", "http://h|model-a", {"a": 1}), "key": "0" * 64}
    _v1(path, tampered)
    before = path.read_text()
    with pytest.raises(MigrationError, match="does not hash back"):
        migrate_v1(path)
    assert path.read_text() == before


def test_migration_needs_a_model_for_entries_recorded_without_one(tmp_path, capsys):
    path = tmp_path / "c.json"
    _v1(path, _v1_entry("one", None, {"a": 1}))
    with pytest.raises(MigrationError, match="--model default="):
        migrate_v1(path)
    assert migrate_main([str(path)]) == 1 and "no recorded model" in capsys.readouterr().err
    assert migrate_main(["--model", "default=model-a", str(path)]) == 0
    entry = json.loads(path.read_text())["entries"][0]
    assert entry["model"] == "model-a" and entry["server"] is None
    assert "1 entries re-keyed" in capsys.readouterr().out


def test_migration_refuses_duplicate_requests(tmp_path):
    path = tmp_path / "c.json"
    e = _v1_entry("one", "http://h|model-a", {"a": 1})
    _v1(path, e, {**e, "response": {"a": 2}})
    with pytest.raises(MigrationError, match="two entries"):
        migrate_v1(path)


# -- spec 021: images are part of the key (digests), never recorded as bytes ----------------------


def test_the_image_digest_is_part_of_the_cassette_key_and_no_bytes_are_stored(tmp_path):
    from pkpipe.evals.cassette import CassetteMiss, CassetteModel
    from pkpipe.ports import ImageInput
    from tests.fakes import FakeModel

    path = tmp_path / "images.json"
    a, b = ImageInput(b"image-a"), ImageInput(b"image-b")
    inner = FakeModel([{"v": "a"}])
    rec = CassetteModel(path, "record", inner=inner, identities={"vision": "http://x|m"})
    assert rec.complete("s", "u", None, profile="vision", images=[a]) == {"v": "a"}
    assert inner.calls[0]["images"] == [a.sha256]
    rec.save()
    text = path.read_text(encoding="utf-8")
    assert a.sha256 in text and "image-a" not in text
    play = CassetteModel(path, "replay", identities={"vision": "http://x|m"})
    assert play.complete("s", "u", None, profile="vision", images=[a]) == {"v": "a"}
    with pytest.raises(CassetteMiss):  # another image, same prompt: a miss, never another image's answer
        play.complete("s", "u", None, profile="vision", images=[b])
    with pytest.raises(CassetteMiss):  # the same prompt without the image is another request
        play.complete("s", "u", None, profile="vision")


def test_a_text_request_keeps_its_key_after_the_images_change():
    from pkpipe.evals.cassette import request_key

    assert request_key("s", "u", None, "p") == request_key("s", "u", None, "p", ())
    assert request_key("s", "u", None, "p", ["abc"]) != request_key("s", "u", None, "p")
