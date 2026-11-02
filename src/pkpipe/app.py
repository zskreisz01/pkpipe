"""Composition root: wires the core (run service, compiler, steps) to the real adapters.

Used by the CLI (``pkpipe run``) and later by ``serve`` (API, app MCP, scheduler). The factory functions
``make_model``, ``make_tools``, ``make_notify``, ``make_classifier`` and ``make_security_log`` are module
attributes on purpose:
they are the single place where an adapter is chosen, and the place an e2e test swaps in a fake.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from pkpipe import telemetry
from pkpipe.adapters.notify_router import ChannelNotifier
from pkpipe.adapters.state_sqlite import SqliteRunStore, open_checkpointer
from pkpipe.adapters.tools_mcp import McpTools
from pkpipe.adapters.tracer_otel import OtelTracer
from pkpipe.engine.registry import load_effective
from pkpipe.engine.runs import RunService
from pkpipe.engine.runtime_defaults import SystemClock
from pkpipe.engine.snapshot import ConfigSnapshot
from pkpipe.ports import ClassifierPort, ContextStorePort, ModelPort, NotifyPort, SecurityLogPort, ToolPort
from pkpipe.steps import StepPorts

DB_ENV = "PKPIPE_DB"
DATA_DIR_ENV = "PKPIPE_DATA_DIR"


def default_db_path() -> Path:
    """``$PKPIPE_DB``, else ``$PKPIPE_DATA_DIR/pkpipe.db``, else ``~/.local/share/pkpipe/pkpipe.db``."""
    if os.environ.get(DB_ENV):
        return Path(os.environ[DB_ENV]).expanduser()
    base = os.environ.get(DATA_DIR_ENV) or "~/.local/share/pkpipe"
    return Path(base).expanduser() / "pkpipe.db"


def make_model(snapshot: ConfigSnapshot, *, timeout_multiplier: float | None = None) -> ModelPort:
    """The OpenAI-compatible model adapter. ``timeout_multiplier`` scales every profile's ``timeout_s``
    (system preflight); ``None`` reads ``PKPIPE_MODEL_TIMEOUT_MULTIPLIER`` at call time (default 1)."""
    from pkpipe.adapters.model_openai import OpenAIModel

    return OpenAIModel(
        snapshot.data.get("models", {}),
        tracer=telemetry.get_telemetry().tracer,
        timeout_multiplier=timeout_multiplier,
    )


def make_tools(snapshot: ConfigSnapshot) -> ToolPort:
    return McpTools(snapshot.data.get("mcp_servers", {}))


def make_notify(snapshot: ConfigSnapshot) -> NotifyPort:
    return ChannelNotifier(snapshot.data.get("channels"), snapshot.assets)


_CLASSIFIERS: dict[str, ClassifierPort] = {}


def make_classifier(snapshot: ConfigSnapshot) -> ClassifierPort:
    """The classifier of ``security.classifier`` (Qwen3Guard, DeBERTa or both; spec 008, 009). Loaded lazily
    on the first scan and kept per configuration, so a run does not reload the models; a missing model
    fails the scan closed."""
    import json

    from pkpipe.adapters.classifiers import build_classifier

    cfg = (snapshot.data.get("security") or {}).get("classifier") or {}
    key = json.dumps(cfg, sort_keys=True)
    if key not in _CLASSIFIERS:
        _CLASSIFIERS[key] = build_classifier(cfg)
    return _CLASSIFIERS[key]


def data_dir() -> Path:
    return Path(os.environ.get(DATA_DIR_ENV) or "~/.local/share/pkpipe").expanduser()


def security_dir(overlay_dir: Path | None) -> Path:
    """Where ``malicious_urls.jsonl`` and the handler's deny-list live: ``<overlay>/security`` (the private
    config repo, so the URL filter of every process reads the same deny-list), else the data directory."""
    return (overlay_dir / "security") if overlay_dir is not None else data_dir() / "security"


def candidates_dir(overlay_dir: Path | None) -> Path:
    """Eval candidates from owner releases (spec 009): ``<overlay>/evals/candidates``, else the data dir."""
    return (overlay_dir / "evals" / "candidates") if overlay_dir is not None else data_dir() / "candidates"


def make_security_log(snapshot: ConfigSnapshot, overlay_dir: Path | None = None) -> SecurityLogPort:
    """The security-flag handler's log and deny-list (spec 009). With an overlay the deny-list is the
    overlay's copy of ``security.url_filter.deny_list`` (started from the pack's list); without one it is
    ``<data dir>/security/deny_list.txt``, which the in-process URL filter merges."""
    from pkpipe.adapters.security_log_files import FileSecurityLog
    from pkpipe.engine.registry import overlay_dir_from_env

    overlay = overlay_dir if overlay_dir is not None else overlay_dir_from_env()
    deny_rel = ((snapshot.data.get("security") or {}).get("url_filter") or {}).get("deny_list")
    directory = security_dir(overlay)
    if overlay is not None and deny_rel:
        return FileSecurityLog(directory, overlay / deny_rel, seed=snapshot.asset(deny_rel))
    return FileSecurityLog(directory)


def make_context_store(overlay_dir: Path | None = None) -> ContextStorePort | None:
    """The overlay's ``contexts/`` and ``security/`` files for the context refresh (spec 011); ``None``
    without an overlay (the public pack is never written: the refresh then fails naming the overlay)."""
    from pkpipe.adapters.context_store_files import FileContextStore
    from pkpipe.engine.registry import overlay_dir_from_env

    overlay = overlay_dir if overlay_dir is not None else overlay_dir_from_env()
    return FileContextStore(overlay) if overlay is not None else None


def ports_for(snapshot: ConfigSnapshot, overlay_dir: Path | None = None) -> StepPorts:
    """Adapters for one pinned config (model profiles and tool servers come from that version)."""
    extra: dict = {
        "classifier": make_classifier(snapshot),
        "security_log": make_security_log(snapshot, overlay_dir),
    }
    store = make_context_store(overlay_dir)
    if store is not None:
        extra["context_store"] = store
    return StepPorts(
        clock=SystemClock(),
        tracer=OtelTracer(),
        model=make_model(snapshot),
        tools=make_tools(snapshot),
        notify=make_notify(snapshot),
        extra=extra,
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
        ports_for=ports or (lambda snapshot: ports_for(snapshot, overlay_dir)),
    )


def configure_telemetry(snapshot: ConfigSnapshot) -> telemetry.Telemetry:
    """OTLP from ``OTEL_*`` env, content capture policy from ``telemetry.json`` (spec 003)."""
    policy = telemetry.ContentPolicy.from_config(snapshot.data.get("telemetry"))
    tel = telemetry.Telemetry.create(policy=policy)
    tel.instrument(langchain=False, httpx=True)
    return telemetry.configure(tel)
