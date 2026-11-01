"""Spec 012 FR-002: ``learning.prepare`` hands only the source URL and the template to the gateway."""

from __future__ import annotations

import pytest

from pkpipe.steps import CODE, RunInfo, StepContext, StepError
from pkpipe.steps.learning_code import find_source_url
from tests.fakes import RecordingTracer
from tests.support.flows import make_ports, make_snapshot

INSTRUCTION = "learning-material/notebook.md"
ASSETS = {f"instructions/{INSTRUCTION}": "  Ten minute overview in English.\n"}


def ctx(block_private=True, assets=ASSETS):
    snap = make_snapshot(
        assets=assets, security={"url_filter": {"block_private_networks": block_private, "max_bytes": 1}}
    )
    tracer = RecordingTracer()
    c = StepContext(
        flow=None,
        node=None,
        run=RunInfo("r", "f", "manual", snap.version),
        snapshot=snap,
        ports=make_ports(tracer=tracer),
        item=None,
        inputs={},
        resolver=lambda ref: None,
    )
    return c, tracer


def note(front=None, body=""):
    return {"front_matter": front or {}, "body": body}


@pytest.mark.parametrize(
    ("front", "body", "expected"),
    [
        ({"source": "https://a.example/x"}, "see https://b.example/y", "https://a.example/x"),
        ({"url": " https://a.example/x "}, "", "https://a.example/x"),
        ({"link": "https://a.example/l"}, "", "https://a.example/l"),
        ({}, "Watch (https://b.example/y), later.", "https://b.example/y"),
        ({"source": 42}, "text https://c.example/z.", "https://c.example/z"),
        ({}, "no link here", None),
        (None, "", None),
    ],
)
def test_find_source_url(front, body, expected):
    assert find_source_url(front, body) == expected


def test_prepare_returns_url_and_trimmed_template_only():
    c, _ = ctx()
    out = CODE["learning.prepare"](
        c,
        note={"front_matter": {"source": "https://a.example/talk"}, "body": "SECRET BODY"},
        instruction=INSTRUCTION,
    )
    assert out.output == {
        "status": "ok",
        "reason": "",
        "url": "https://a.example/talk",
        "template": "Ten minute overview in English.",
    }
    assert out.item is None
    assert "SECRET BODY" not in repr(out.output)  # constitution IV: the note text goes nowhere


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("http://127.0.0.1:8080/admin", "private_network"),
        ("http://169.254.169.254/latest", "private_network"),
        ("file:///etc/passwd", "scheme_not_allowed"),
        ("javascript:alert(1)", "scheme_not_allowed"),
    ],
)
def test_blocked_url_flags_the_item_and_traces_the_flag(source, reason):
    """BPMN URL check -> Security flag: no URL leaves the flow, the item is flagged for quarantine."""
    c, tracer = ctx()
    out = CODE["learning.prepare"](c, note=note({"source": source}), instruction=INSTRUCTION)
    assert out.output["status"] == "blocked" and out.output["reason"] == reason
    assert out.output["url"] == "" and out.output["template"] == ""  # nothing to forward
    assert out.item == {
        "flagged": True,
        "security_flags": [{"detector": "url_filter", "reason_code": reason}],
    }
    assert tracer.flags == [{"detector": "url_filter", "action": "flag", "reason_code": reason}]


def test_private_network_rule_follows_the_security_config():
    c, _ = ctx(block_private=False)
    out = CODE["learning.prepare"](
        c, note=note({"source": "http://127.0.0.1:8765/p"}), instruction=INSTRUCTION
    )
    assert out.output["status"] == "ok"


def test_note_without_url_is_not_a_security_flag():
    c, tracer = ctx()
    out = CODE["learning.prepare"](c, note=note(body="just text"), instruction=INSTRUCTION)
    assert out.output["status"] == "no_url" and out.item is None and tracer.flags == []


def test_missing_instruction_asset_is_an_error():
    c, _ = ctx(assets={})
    with pytest.raises(StepError, match="not found"):
        CODE["learning.prepare"](c, note=note({"source": "https://a.example/x"}), instruction=INSTRUCTION)
