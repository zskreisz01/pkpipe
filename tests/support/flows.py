"""Builders for flow tests: synthetic config snapshots, fake ports and a run service on fakes."""

from __future__ import annotations

import copy
import json
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from pkpipe.engine.registry import snapshot_version
from pkpipe.engine.runs import RunService
from pkpipe.engine.snapshot import ConfigSnapshot
from pkpipe.steps import StepPorts
from tests.fakes import FakeClock, FakeModel, FakeNotify, FakeTools, InMemoryRunStore, RecordingTracer

ASSETS = {
    "prompts/system/analyst.md": "You are an analyst.",
    "instructions/demo/summarize.md": "Summarize the inputs.",
    "schemas/verdict.json": json.dumps(
        {"type": "object", "required": ["verdict"], "properties": {"verdict": {"type": "string"}}}
    ),
}


def make_snapshot(
    *flows: dict[str, Any],
    assets: dict[str, str] | None = None,
    allow: list[str] | None = None,
    **extra: Any,
) -> ConfigSnapshot:
    data: dict[str, Any] = {
        "flows": {f["id"]: copy.deepcopy(f) for f in flows},
        "mcp_servers": {
            "t": {
                "transport": "stdio",
                "command": "unused",
                "allow": allow or ["read", "status", "done", "fail"],
            }
        },
        "channels": {"channels": {"main": {"type": "log"}}, "routes": {"ready": ["main"]}},
        "categories": [
            {
                "id": "tool",
                "label": "Tools",
                "subflow": "other",
                "para_path": "3-Resources/Tools",
                "triage_hint": "x",
            },
            {
                "id": "misc",
                "label": "Misc",
                "subflow": "other",
                "para_path": "3-Resources/Misc",
                "triage_hint": "y",
            },
        ],
    }
    data.update(extra)
    a = dict(ASSETS if assets is None else assets)
    return ConfigSnapshot(snapshot_version(data, a), data, a)


def make_ports(*, model=None, tools=None, notify=None, clock=None, tracer=None, **extra: Any) -> StepPorts:
    """Fake ports. Unless given (``classifier=None`` switches it off), the spec 009 ports are fakes too:
    ``classifier`` (substring rules, :func:`tests.support.evals.fake_classifier`) and ``security_log``."""
    from tests.fakes import FakeSecurityLog
    from tests.support.evals import fake_classifier

    extra.setdefault("classifier", fake_classifier())
    extra.setdefault("security_log", FakeSecurityLog())
    return StepPorts(
        clock=clock or FakeClock(),
        tracer=tracer or RecordingTracer(),
        model=model if model is not None else FakeModel(),
        tools=tools if tools is not None else FakeTools(),
        notify=notify if notify is not None else FakeNotify(routes={"ready": ["main"]}),
        extra=extra,
    )


def make_service(
    config, ports: StepPorts, *, store=None, checkpointer=None, registry=None, snapshots_for=None
) -> RunService:
    provider = config if callable(config) else (lambda: config)
    return RunService(
        store=store or InMemoryRunStore(),
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
        config=provider,
        ports_for=lambda snapshot: ports,
        registry=registry,
        snapshots_for=snapshots_for,
    )
