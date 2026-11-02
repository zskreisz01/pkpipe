"""Spec 011: the enterprise (and personal) notes are summarized by the local model only. No remote model
can be selected for the context refresh: not by a profile, a node override, a fallback chain, the
environment, or by dropping the flow's data class. The shipped flows are the subject, changed the way an
owner's overlay could."""

from __future__ import annotations

import copy
import json

import pytest

from pkpipe.engine.compiler import private_data_errors, validate_config_flows
from pkpipe.engine.registry import load_effective
from pkpipe.engine.snapshot import ConfigSnapshot

REMOTE = {"base_url": "https://api.example.invalid/v1", "model": "hosted"}
FLOWS = ("context_refresh", "context_pack")


@pytest.fixture
def base(pack_dir):
    return load_effective(pack_dir, None).snapshot()


def changed(snapshot: ConfigSnapshot, edit) -> ConfigSnapshot:
    data = copy.deepcopy(snapshot.data)
    edit(data)
    return ConfigSnapshot(snapshot.version, data, dict(snapshot.assets))


def errors(snapshot: ConfigSnapshot) -> list[str]:
    found, _ = validate_config_flows(snapshot)
    return [e for e in found if "context" in e]


def node(data, flow, node_id):
    return next(n for n in data["flows"][flow]["nodes"] if n["id"] == node_id)


def test_the_shipped_flows_are_context_flows_without_tools_or_egress(base):
    assert errors(base) == []
    for fid in FLOWS:
        flow = base.flow(fid)
        assert "context" in flow.data_classes
        assert not [n for n in flow.nodes if n.type == "llm_tools"]
    sessions = [n for n in base.flow("context_pack").nodes if n.type == "llm"]
    assert {n.id for n in sessions} == {"summarize_personal", "summarize_enterprise"}


def test_a_remote_default_profile_is_refused(base):
    bad = changed(base, lambda d: d["models"]["default"].update(REMOTE))
    found = errors(bad)
    assert found and all("not on a loopback address" in e for e in found)
    assert any("summarize_enterprise" in e for e in found)


def test_an_overlay_that_selects_a_remote_profile_for_the_enterprise_session_is_refused(base):
    def edit(d):
        d["models"]["cloud"] = REMOTE
        node(d, "context_pack", "summarize_enterprise")["config"]["profile"] = "cloud"

    found = errors(changed(base, edit))
    assert len(found) == 1 and "summarize_enterprise" in found[0] and "profile 'cloud'" in found[0]


def test_a_remote_fallback_of_the_local_profile_is_refused(base):
    def edit(d):
        d["models"]["cloud"] = REMOTE
        d["models"]["default"]["fallback"] = "cloud"

    assert any("profile 'cloud'" in e for e in errors(changed(base, edit)))


def test_dropping_the_data_class_does_not_lift_the_rules(base):
    """An overlay that empties ``data_classes`` would otherwise make the flow a normal one (remote model,
    trace content capture): the context functions require the class, so validation fails."""

    def edit(d):
        for fid in FLOWS:
            d["flows"][fid]["data_classes"] = []

    found = errors(changed(base, edit))
    assert found and all("must declare data class 'context'" in e for e in found)


def test_an_egress_tool_cannot_be_added_to_the_refresh(base):
    def edit(d):
        d["flows"]["context_pack"]["nodes"].append(
            {
                "id": "leak",
                "type": "tool",
                "config": {"tool": "websearch:search", "arguments": {"query": "x"}},
            }
        )

    assert any("sends data off the machine" in e for e in errors(changed(base, edit)))


def test_a_session_with_tools_cannot_be_added_to_the_refresh(base):
    def edit(d):
        d["flows"]["context_pack"]["nodes"].append(
            {
                "id": "browse",
                "type": "llm_tools",
                "config": {
                    "system": "context_analyst",
                    "instruction": "context/enterprise.md",
                    "tools": ["websearch:search"],
                },
            }
        )

    assert any("session with tools" in e for e in errors(changed(base, edit)))


def test_the_refresh_only_starts_context_subflows(base):
    def edit(d):
        d["flows"]["context_pack"]["data_classes"] = ["journal"]

    found = errors(changed(base, edit))
    assert any("context_refresh" in e and "not a context flow" in e for e in found)


@pytest.mark.parametrize("value", ["https://api.example.invalid/v1", "http://192.168.1.20:1234/v1"])
def test_an_environment_pointing_the_model_off_the_machine_is_refused_at_run_time(base, value):
    for fid in FLOWS:
        found = private_data_errors(base.flow(fid), base, {"PKPIPE_MODEL_BASE_URL": value})
        assert found and "context data is processed locally only" in found[0] and value not in found[0]
        assert (
            private_data_errors(base.flow(fid), base, {"PKPIPE_MODEL_BASE_URL": "http://localhost:1/v1"})
            == []
        )


def test_the_shipped_flow_json_declares_the_class():
    for fid in FLOWS:
        text = json.loads(open(f"packs/learning-inbox/flows/{fid}.json").read())
        assert text["data_classes"] == ["context"]
