"""Spec 015 FR-002 / constitution VII: flows that handle journal data are local only (validation)."""

from __future__ import annotations

import pytest

from pkpipe.engine.compiler import validate_config_flows
from pkpipe.engine.registry import load_effective
from tests.support.flows import make_snapshot

ASSETS = {
    "prompts/system/analyst.md": "You are an analyst.",
    "instructions/demo/summarize.md": "Summarize.",
}
LOCAL = {"base_url": "env:PKPIPE_MODEL_BASE_URL|http://127.0.0.1:1234/v1", "model": "m"}
REMOTE = {"base_url": "https://api.example.invalid/v1", "model": "m"}


def _flow(*nodes, data_classes=("journal",), fid="j"):
    return {
        "id": fid,
        "data_classes": list(data_classes),
        "start": nodes[0]["id"],
        "nodes": list(nodes),
        "edges": [],
    }


def _llm(profile="default", **kw):
    return {
        "id": "s",
        "type": "llm",
        "config": {"system": "analyst", "instruction": "demo/summarize.md", "profile": profile},
        **kw,
    }


def _errors(*flows, models=None, servers=None):
    extra = {"models": models or {"default": LOCAL}}
    snap = make_snapshot(*flows, assets=ASSETS, **extra)
    if servers is not None:
        snap.data["mcp_servers"] = servers
    errors, _ = validate_config_flows(snap)
    return [e for e in errors if "journal data" in e or "journal flow" in e]


def test_the_shipped_pack_validates_and_its_journal_flows_are_local(pack_dir):
    snap = load_effective(pack_dir, None).snapshot()
    errors, _ = validate_config_flows(snap)
    assert errors == []
    for fid in ("improvement_extract", "improvement_review", "review_improvement"):
        assert "journal" in snap.flow(fid).data_classes


def test_local_session_passes():
    assert _errors(_flow(_llm())) == []


@pytest.mark.parametrize(
    "base_url",
    ["https://api.example.invalid/v1", "env:PKPIPE_MODEL_BASE_URL", "env:X|https://api.example.invalid/v1"],
)
def test_a_session_on_a_remote_or_unknown_model_endpoint_is_refused(base_url):
    errors = _errors(_flow(_llm()), models={"default": {**REMOTE, "base_url": base_url}})
    assert len(errors) == 1 and "not on a loopback address" in errors[0]


def test_a_remote_fallback_is_refused_too():
    models = {"default": {**LOCAL, "fallback": "cloud"}, "cloud": REMOTE}
    assert any("profile 'cloud'" in e for e in _errors(_flow(_llm()), models=models))


def test_other_flows_may_use_remote_models():
    assert _errors(_flow(_llm(), data_classes=()), models={"default": REMOTE}) == []


def test_journal_flows_have_no_tool_sessions():
    node = {
        "id": "t",
        "type": "llm_tools",
        "config": {"system": "analyst", "instruction": "demo/summarize.md", "tools": ["t:read"]},
    }
    assert any("session with tools" in e for e in _errors(_flow(node)))


def test_journal_flows_do_not_call_egress_servers():
    servers = {
        "web": {"transport": "stdio", "command": "x", "allow": ["search"], "egress": True},
        "vault": {"transport": "stdio", "command": "x", "allow": ["read"]},
    }
    bad = {"id": "t", "type": "tool", "config": {"tool": "web:search"}}
    good = {"id": "g", "type": "tool", "config": {"tool": "vault:read"}}
    assert any("sends data off the machine" in e for e in _errors(_flow(bad), servers=servers))
    assert _errors(_flow(good), servers=servers) == []


def test_journal_flows_only_start_journal_subflows():
    sub = {"id": "sub", "type": "subflow", "config": {"flow": "other"}}
    other = _flow(_llm(), data_classes=(), fid="other")
    assert any("not a journal flow" in e for e in _errors(_flow(sub), other))
    journal_other = _flow(_llm(), fid="other")
    assert _errors(_flow(sub), journal_other) == []
