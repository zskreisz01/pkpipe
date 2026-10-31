"""Spec 001 T005: every port has an in-memory fake that honours the port contract."""

from __future__ import annotations

import inspect

import pytest

from pkpipe import ports
from pkpipe.engine.registry import config_version
from tests.fakes import FakeModel, FakeNotify, FakeTools, InMemoryConfig, InMemoryState

FAKES = {
    "ModelPort": FakeModel(),
    "ToolPort": FakeTools(),
    "NotifyPort": FakeNotify(),
    "StatePort": InMemoryState(),
    "ConfigPort": InMemoryConfig(),
}


def test_every_port_has_a_fake():
    port_names = {n for n, o in vars(ports).items() if inspect.isclass(o) and n.endswith("Port")}
    assert port_names == set(FAKES), "add a fake in tests/fakes/ for every new port"


@pytest.mark.parametrize("name", sorted(FAKES))
def test_fake_satisfies_its_port(name):
    assert isinstance(FAKES[name], getattr(ports, name))


def test_fake_model_replays_script_and_records_calls():
    m = FakeModel([{"category": "tool"}, {"category": "misc"}])
    assert m.complete("sys", "u1", {"type": "object"}) == {"category": "tool"}
    assert m.complete("sys", "u2") == {"category": "misc"}
    assert [c["user"] for c in m.calls] == ["u1", "u2"] and m.calls[0]["json_schema"] == {"type": "object"}
    with pytest.raises(AssertionError, match="no scripted reply"):
        m.complete("sys", "u3")


def test_fake_model_callable_script():
    assert FakeModel(lambda s, u: {"echo": u}).complete("s", "hi") == {"echo": "hi"}


def test_fake_tools_list_respects_allow_list_and_call_records():
    t = FakeTools({"read_note": lambda path: f"body of {path}", "write_note": lambda path, body: None})
    assert t.list_tools(["read_note"]) == ["read_note"]
    assert t.list_tools([]) == []
    assert t.call("read_note", {"path": "a.md"}) == "body of a.md"
    assert t.calls == [("read_note", {"path": "a.md"})]
    with pytest.raises(KeyError):
        t.call("delete_everything", {})


def test_fake_notify_collects_per_channel():
    n = FakeNotify()
    n.send("discord_main", "triage ready", [{"id": "confirm", "label": "Confirm"}])
    n.send("other", "x")
    assert [s.message for s in n.to("discord_main")] == ["triage ready"]
    assert n.to("discord_main")[0].actions == [{"id": "confirm", "label": "Confirm"}]


def test_in_memory_state_roundtrip_isolates_copies():
    s = InMemoryState()
    assert s.get_item("1") is None
    item = {"id": "1", "state": "pending", "notes": []}
    s.save_item(item)
    item["state"] = "mutated-after-save"
    loaded = s.get_item("1")
    assert loaded["state"] == "pending"
    loaded["notes"].append("x")
    assert s.get_item("1")["notes"] == []


def test_in_memory_config_version_follows_effective_content():
    a = InMemoryConfig({"schedules": [{"flow": "t", "cron": "0 1 * * *"}]})
    b = InMemoryConfig(
        {"schedules": [{"flow": "t", "cron": "0 1 * * *"}]},
        {"schedules": [{"flow": "t", "cron": "0 2 * * *"}]},
    )
    assert a.version() == config_version({"schedules": [{"flow": "t", "cron": "0 1 * * *"}]})
    assert a.version() != b.version()
    assert len(b.load_layers()) == 2


def test_fake_model_accepts_profile_raises_scripted_errors_and_can_check_schema():
    from pkpipe.ports import ModelUnavailable, SchemaError

    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    m = FakeModel([ModelUnavailable("down"), {"n": 1}, {"n": "x"}], schema_check=True)
    with pytest.raises(ModelUnavailable):
        m.complete("s", "u", schema, profile="judge")
    assert m.complete("s", "u", schema) == {"n": 1}
    with pytest.raises(SchemaError):
        m.complete("s", "u", schema)
    assert [c["profile"] for c in m.calls] == ["judge", "default", "default"]
    assert FakeModel([SchemaError("x")]).calls == []
