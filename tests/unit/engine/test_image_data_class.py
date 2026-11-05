"""Spec 021 T008 (FR-009): the data class ``image``: loopback model profiles only, no egress tool, no trace
content, never a remote profile or fallback. Validation (config) and compile (every run) both refuse."""

from __future__ import annotations

import pytest

from pkpipe.domain.data_classes import PRIVATE_DATA_CLASSES, is_private
from pkpipe.engine.compiler import FlowCompiler, FlowError, private_data_errors, validate_config_flows
from pkpipe.engine.registry import load_effective
from pkpipe.steps import RunInfo
from pkpipe.telemetry import ContentPolicy
from tests.support.flows import make_ports, make_snapshot

ASSETS = {
    "prompts/system/viewer.md": "You are the Viewer.",
    "instructions/images/describe.md": "Describe.",
    "schemas/image_description.json": '{"type": "object"}',
}
LOCAL_VISION = {
    "base_url": "env:PKPIPE_MODEL_BASE_URL|http://127.0.0.1:1234/v1",
    "model": "vm",
    "vision": True,
    "max_tokens": 1200,
}
REMOTE_VISION = {
    "base_url": "https://api.example.invalid/v1",
    "model": "vm",
    "vision": True,
    "max_tokens": 1200,
}


def _vision(profile="vision", **kw):
    return {
        "id": "describe",
        "type": "vision",
        "config": {
            "system": "viewer",
            "instruction": "images/describe.md",
            "output_schema": "image_description",
            "profile": profile,
            "canary": True,
            "image": "prep",
            **kw,
        },
    }


def _flow(*nodes, data_classes=("image",), fid="img"):
    guard = {"id": "guard", "type": "output_guard", "config": {"input": ["describe"]}}
    prep = {"id": "prep", "type": "code", "config": {"function": "image.prepare"}}
    flag = {"id": "flag", "type": "code", "config": {"function": "item.set", "args": {"flagged": True}}}
    return {
        "id": fid,
        "data_classes": list(data_classes),
        "start": "prep",
        "nodes": [prep, *nodes, guard, flag],
        "edges": [
            {"from": "prep", "to": nodes[0]["id"]},
            {"from": nodes[0]["id"], "to": "guard"},
            {"from": "guard", "to": "flag", "when": {"path": "guard.flagged", "truthy": True}},
        ],
    }


def _errors(*flows, models=None, servers=None, images=None):
    snap = make_snapshot(
        *flows, assets=ASSETS, models=models or {"vision": LOCAL_VISION}, images=images or {}
    )
    if servers is not None:
        snap.data["mcp_servers"] = servers
    errors, _ = validate_config_flows(snap)
    return errors


def test_the_image_class_is_private_like_journal_and_context():
    assert {"journal", "context", "image"} == PRIVATE_DATA_CLASSES
    assert (
        is_private(["image"]) and is_private(("x", "image")) and not is_private([]) and not is_private(None)
    )


def test_a_local_vision_flow_validates():
    assert _errors(_flow(_vision())) == []


@pytest.mark.parametrize(
    "base_url",
    ["https://api.example.invalid/v1", "env:PKPIPE_MODEL_BASE_URL", "env:X|https://api.example.invalid/v1"],
)
def test_a_vision_session_on_a_remote_or_unknown_endpoint_is_refused(base_url):
    errors = _errors(_flow(_vision()), models={"vision": {**LOCAL_VISION, "base_url": base_url}})
    assert any("not on a loopback address" in e and "image data" in e for e in errors)


def test_a_remote_fallback_of_the_vision_profile_is_refused():
    models = {"vision": {**LOCAL_VISION, "fallback": "cloud"}, "cloud": REMOTE_VISION}
    assert any("profile 'cloud'" in e for e in _errors(_flow(_vision()), models=models))


def test_the_category_session_of_an_image_flow_is_local_too():
    llm = {
        "id": "suggest",
        "type": "llm",
        "config": {"system": "viewer", "instruction": "images/describe.md", "profile": "cloud"},
    }
    models = {"vision": LOCAL_VISION, "cloud": REMOTE_VISION}
    flow = _flow(_vision(), llm)
    flow["edges"].append({"from": "flag", "to": "suggest"})
    assert any("profile 'cloud'" in e for e in _errors(flow, models=models))


def test_an_image_flow_calls_no_egress_server():
    servers = {"web": {"transport": "stdio", "command": "x", "allow": ["search"], "egress": True}}
    tool = {"id": "t", "type": "tool", "config": {"tool": "web:search"}}
    flow = _flow(_vision(), tool)
    flow["edges"].append({"from": "flag", "to": "t"})
    assert any("sends data off the machine" in e for e in _errors(flow, servers=servers))


def test_an_image_flow_has_no_session_with_tools():
    node = {
        "id": "t",
        "type": "llm_tools",
        "config": {"system": "viewer", "instruction": "images/describe.md", "tools": ["x:y"]},
    }
    flow = _flow(_vision(), node)
    flow["edges"].append({"from": "flag", "to": "t"})
    assert any("session with tools" in e for e in _errors(flow))


def test_an_image_flow_only_starts_image_subflows():
    sub = {"id": "sub", "type": "subflow", "config": {"flow": "other"}}
    other = _flow(_vision(), data_classes=(), fid="other")
    flow = _flow(_vision(), sub)
    flow["edges"].append({"from": "flag", "to": "sub"})
    assert any("not a image flow" in e for e in _errors(flow, other))


def test_a_vision_session_needs_the_image_class_a_vision_profile_and_a_hard_token_limit():
    no_class = _errors(_flow(_vision(), data_classes=()))
    assert any("must declare data class 'image'" in e for e in no_class)
    no_vision = _errors(_flow(_vision()), models={"vision": {**LOCAL_VISION, "vision": False}})
    assert any("does not declare vision: true" in e for e in no_vision)
    unlimited = {k: v for k, v in LOCAL_VISION.items() if k != "max_tokens"}
    assert any("needs a max_tokens" in e for e in _errors(_flow(_vision()), models={"vision": unlimited}))
    too_much = {**LOCAL_VISION, "max_tokens": 4096}
    assert any(
        "exceeds images.max_tokens 1200" in e for e in _errors(_flow(_vision()), models={"vision": too_much})
    )


def test_a_vision_session_must_place_a_canary_and_be_followed_by_the_output_guard():
    assert any("canary must be true" in e for e in _errors(_flow(_vision(canary=False))))
    flow = _flow(_vision())
    flow["nodes"] = [n for n in flow["nodes"] if n["id"] != "guard"]
    flow["edges"] = [
        {"from": "prep", "to": "describe"},
        {"from": "describe", "to": "flag"},
    ]
    assert any("output_guard" in e for e in _errors(flow))


def test_the_image_code_functions_need_the_image_class():
    flow = _flow(_vision(), data_classes=())
    errors = _errors(flow)
    assert any("'image.prepare' handles image data" in e for e in errors)


def test_the_output_of_the_vision_session_is_never_an_input_of_a_session_with_tools():
    tools = {
        "id": "t",
        "type": "llm_tools",
        "inputs": ["describe.topic"],
        "config": {"system": "viewer", "instruction": "images/describe.md", "tools": ["v:read"]},
    }
    flow = {
        "id": "x",
        "start": "describe",
        "nodes": [_vision(), tools],
        "edges": [{"from": "describe", "to": "t"}],
    }
    errors = _errors(flow, servers={"v": {"transport": "stdio", "command": "x", "allow": ["read"]}})
    assert any("must not receive raw untrusted input" in e for e in errors)


def _runtime(flow, environ, models=None):
    snap = make_snapshot(flow, assets=ASSETS, models=models or {"vision": LOCAL_VISION}, images={})
    return private_data_errors(snap.flow(flow["id"]), snap, environ)


def test_an_environment_value_pointing_the_vision_profile_off_the_machine_is_refused_at_run_time():
    value = "https://api.example.invalid/v1"
    assert _runtime(_flow(_vision()), {}) == []
    errors = _runtime(_flow(_vision()), {"PKPIPE_MODEL_BASE_URL": value})
    assert len(errors) == 1 and "non-loopback" in errors[0] and value not in errors[0]


def test_the_compiler_refuses_the_flow_before_any_image_is_sent(monkeypatch):
    monkeypatch.setenv("PKPIPE_MODEL_BASE_URL", "https://api.example.invalid/v1")
    snap = make_snapshot(_flow(_vision()), assets=ASSETS, models={"vision": LOCAL_VISION}, images={})
    compiler = FlowCompiler(snap, make_ports(), RunInfo("r", "img", "manual", snap.version))
    with pytest.raises(FlowError, match="non-loopback"):
        compiler.compile("img")


def test_content_capture_is_forced_off_for_the_image_class_even_when_the_flow_is_listed():
    policy = ContentPolicy(frozenset({"img", "other"}))
    assert policy.capture_for("img", ("image",)) is False
    assert policy.capture_for("other", ()) is True


def test_the_shipped_pack_image_flows_are_local_and_validate(pack_dir):
    snap = load_effective(pack_dir, None).snapshot()
    errors, _ = validate_config_flows(snap)
    assert errors == []
    for fid in ("describe_images", "describe_image_item", "handle_security_flag"):
        assert "image" in snap.flow(fid).data_classes
    vision = snap.data["models"]["vision"]
    assert vision["vision"] is True and vision["max_tokens"] == 1200 and vision["timeout_s"] == 300
    assert vision["model"] == "env:PKPIPE_VISION_MODEL|google/gemma-4-12b" and vision["retries"] == 1
    assert vision["base_url"] == snap.data["models"]["default"]["base_url"]
    assert snap.data["images"] == {
        "time_budget_s": 7200,
        "estimate_s": 60,
        "per_image_timeout_s": 300,
        "max_side": 1024,
        "max_tokens": 1200,
        "max_bytes": 10485760,
        "retries": 1,
    }


def test_a_vision_profile_timeout_above_the_image_timeout_is_a_warning():
    snap = make_snapshot(
        _flow(_vision()), assets=ASSETS, models={"vision": {**LOCAL_VISION, "timeout_s": 900}}, images={}
    )
    errors, warnings = validate_config_flows(snap)
    assert errors == []
    assert any("exceeds images.per_image_timeout_s 300" in w for w in warnings)
    ok = make_snapshot(
        _flow(_vision()), assets=ASSETS, models={"vision": {**LOCAL_VISION, "timeout_s": 300}}, images={}
    )
    assert not any("per_image_timeout_s" in w for w in validate_config_flows(ok)[1])


def _read_image_flow(data_classes):
    tool = {"id": "rd", "type": "tool", "config": {"tool": "v:read_image", "arguments": {"name": "a.png"}}}
    return {"id": "rdflow", "data_classes": list(data_classes), "start": "rd", "nodes": [tool], "edges": []}


def test_a_flow_that_calls_read_image_must_declare_the_image_class():
    servers = {"v": {"transport": "stdio", "command": "x", "allow": ["read_image"]}}
    errors = _errors(_read_image_flow(()), servers=servers)
    assert any("'v:read_image' reads an image" in e and "data class 'image'" in e for e in errors)
    assert _errors(_read_image_flow(("image",)), servers=servers) == []
