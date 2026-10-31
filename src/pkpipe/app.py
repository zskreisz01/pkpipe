"""Composition root: wires the core (run service, compiler, steps) to the real adapters.

Used by the CLI (``pkpipe run``) and later by ``serve`` (API, app MCP, scheduler). The factory functions
``make_model``, ``make_tools`` and ``make_notify`` are module attributes on purpose: they are the single
place where an adapter is chosen, and the place an e2e test swaps in a fake.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from pkpipe import telemetry
from pkpipe.adapters.notify_log import LogNotify
from pkpipe.adapters.state_sqlite import SqliteRunStore, open_checkpointer
from pkpipe.adapters.tools_mcp import McpTools
from pkpipe.adapters.tracer_otel import OtelTracer
from pkpipe.engine.registry import load_effective
from pkpipe.engine.runs import RunService
from pkpipe.engine.runtime_defaults import SystemClock
from pkpipe.engine.snapshot import ConfigSnapshot
from pkpipe.ports import ModelPort, NotifyPort, ToolPort
from pkpipe.steps import StepPorts

DB_ENV = "PKPIPE_DB"
DATA_DIR_ENV = "PKPIPE_DATA_DIR"


def default_db_path() -> Path:
    """``$PKPIPE_DB``, else ``$PKPIPE_DATA_DIR/pkpipe.db``, else ``~/.local/share/pkpipe/pkpipe.db``."""
    if os.environ.get(DB_ENV):
        return Path(os.environ[DB_ENV]).expanduser()
    base = os.environ.get(DATA_DIR_ENV) or "~/.local/share/pkpipe"
    return Path(base).expanduser() / "pkpipe.db"


def make_model(snapshot: ConfigSnapshot) -> ModelPort:
    from pkpipe.adapters.model_openai import OpenAIModel

    return OpenAIModel(snapshot.data.get("models", {}), tracer=telemetry.get_telemetry().tracer)


def make_tools(snapshot: ConfigSnapshot) -> ToolPort:
    return McpTools(snapshot.data.get("mcp_servers", {}))


def make_notify(snapshot: ConfigSnapshot) -> NotifyPort:
    return LogNotify()  # replaced by channel adapters in spec 006


def ports_for(snapshot: ConfigSnapshot) -> StepPorts:
    """Adapters for one pinned config (model profiles and tool servers come from that version)."""
    return StepPorts(
        clock=SystemClock(),
        tracer=OtelTracer(),
        model=make_model(snapshot),
        tools=make_tools(snapshot),
        notify=make_notify(snapshot),
    )


def build_run_service(
    pack_dir: Path | None = None,
    overlay_dir: Path | None = None,
    db_path: Path | None = None,
    *,
    ports: Callable[[ConfigSnapshot], StepPorts] | None = None,
) -> RunService:
    db = db_path or default_db_path()
    return RunService(
        store=SqliteRunStore(db),
        checkpointer=open_checkpointer(db),
        config=lambda: load_effective(pack_dir, overlay_dir).snapshot(),
        ports_for=ports or ports_for,
    )


def configure_telemetry(snapshot: ConfigSnapshot) -> telemetry.Telemetry:
    """OTLP from ``OTEL_*`` env, content capture policy from ``telemetry.json`` (spec 003)."""
    policy = telemetry.ContentPolicy.from_config(snapshot.data.get("telemetry"))
    tel = telemetry.Telemetry.create(policy=policy)
    tel.instrument(langchain=False, httpx=True)
    return telemetry.configure(tel)
