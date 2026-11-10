"""``pkpipe serve``: API, UI, scheduler and app MCP server in one process (spec 014 FR-001, FR-002).

The UI and the MCP endpoint share one authenticated listener on 127.0.0.1 (default) or a Tailscale address.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from pkpipe.engine.snapshot import ConfigSnapshot
from pkpipe.evals.gate import PublishGate

log = logging.getLogger("pkpipe.serve")
VAULT_ENV = "PKPIPE_VAULT_DIR"
DEFAULT_PORT = 8787


def _gate_classifier(snapshot: ConfigSnapshot):
    """The runs' classifier for the security suite of the publish gate, or None when it is not installed
    (the suite then notes that it measured without it; its classifier gate fails closed)."""
    from pkpipe import app
    from pkpipe.adapters.classifiers import is_available

    clf = app.make_classifier(snapshot)
    return clf if is_available(clf) else None


def default_gate_factory(overlay_dir: Path | None) -> Callable[[ConfigSnapshot], PublishGate]:
    """Gate on the configured model; datasets from the public ``evals/`` plus the overlay's ``evals/``."""

    def make(snapshot: ConfigSnapshot) -> PublishGate:
        from pkpipe import app
        from pkpipe.evals.datasets import EvalPaths

        return PublishGate(
            model=app.make_model(snapshot),
            paths=EvalPaths.create(None, overlay_dir),
            options={"classifier": _gate_classifier(snapshot)},
        )

    return make


def build_services(
    pack_dir: Path | None = None,
    overlay_dir: Path | None = None,
    db_path: Path | None = None,
    vault_dir: Path | None = None,
    *,
    run_service=None,
    gate_factory: Callable[[ConfigSnapshot], PublishGate] | None = None,
    snapshots=None,
):
    """Wire the use cases to the real adapters (tests pass their own ``run_service`` and ``gate_factory``)."""
    from pkpipe import app
    from pkpipe.adapters.app_store_sqlite import AppStore
    from pkpipe.api.publish import Publisher
    from pkpipe.api.services import AppServices, RunLauncher, default_vault
    from pkpipe.api.workspace import ConfigWorkspace
    from pkpipe.engine.registry import default_pack_dir, overlay_dir_from_env

    pack = pack_dir or default_pack_dir()
    overlay = overlay_dir if overlay_dir is not None else overlay_dir_from_env()
    db = db_path or app.default_db_path()
    runs = run_service or app.build_run_service(pack, overlay, db)
    store = AppStore(db)
    workspace = ConfigWorkspace(pack, overlay)
    vault_path = vault_dir if vault_dir is not None else _env_path(VAULT_ENV)
    vault = default_vault(vault_path, app.vault_policy_for(runs.config(), vault_path))
    if snapshots is None:  # the restore of spec 019: same vault policy as the runs' snapshots
        snapshots = app.make_snapshots(runs.config(), db, vault_path)
    return AppServices(
        runs=runs,
        store=store,
        workspace=workspace,
        publisher=Publisher(workspace, store),
        launcher=RunLauncher(runs),
        vault=vault,
        gate_factory=gate_factory or default_gate_factory(overlay),
        candidates_dir=app.candidates_dir(overlay),
        snapshots=snapshots,
        image_preparer=_image_preparer(app),
        learning_jobs=app.make_learning_jobs(runs.config().data),
    )


def _image_preparer(app):
    """The Pillow preparer for UI thumbnails, or None without the optional extra ``images``."""
    from pkpipe.adapters.image_pillow import pillow_available

    return app.make_image_preparer() if pillow_available() else None


def ui_settings(data: dict, extra_hosts: tuple[str, ...] = ()) -> tuple[tuple[str, ...], str | None]:
    """``(Host names the UI answers to, public URL)`` from the effective config (``ui.json``, spec 023) and
    ``--allow-host``: the MagicDNS name, the Tailscale IP and the host of ``public_url``."""
    from pkpipe.engine.config_models import UiConfig

    ui = UiConfig.model_validate(data.get("ui") or {})
    hosts = tuple(dict.fromkeys([*ui.hosts(), *(h.strip().lower() for h in extra_hosts if h.strip())]))
    return hosts, ui.public_url


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", default="127.0.0.1", help="listen address: loopback (default) or a Tailscale IP")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--pack", help="pack directory (default: packs/learning-inbox or PKPIPE_PACK_DIR)")
    p.add_argument(
        "--overlay", help="private overlay = the editable config repo (default: PKPIPE_OVERLAY_DIR)"
    )
    p.add_argument("--db", help="app DB (default: PKPIPE_DB or $PKPIPE_DATA_DIR/pkpipe.db)")
    p.add_argument("--vault", help=f"Obsidian vault folder for triage (default: {VAULT_ENV})")
    p.add_argument("--allow-host", action="append", default=[], help="extra Host name to accept (MagicDNS)")
    p.add_argument("--no-scheduler", action="store_true", help="do not start the cron scheduler")


def serve_command(args: argparse.Namespace) -> int:
    import threading

    import uvicorn

    from pkpipe import app as composition
    from pkpipe.api.app import create_app
    from pkpipe.api.security import BindRefused, Tokens, check_bind
    from pkpipe.engine.registry import ConfigError, load_effective
    from pkpipe.scheduler import FlowScheduler

    try:
        host = check_bind(args.host)
    except BindRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    pack = Path(args.pack) if args.pack else None
    overlay = Path(args.overlay) if args.overlay else None
    try:
        cfg = load_effective(pack, overlay)
    except ConfigError as exc:
        for err in exc.errors:
            print(f"error: {err}", file=sys.stderr)
        return 1
    db = Path(args.db) if args.db else composition.default_db_path()
    try:
        tokens = Tokens.load(db.parent)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    tel = composition.configure_telemetry(cfg.snapshot())
    services = build_services(pack, overlay, db, Path(args.vault).expanduser() if args.vault else None)
    scheduler = None
    if not args.no_scheduler:
        scheduler = FlowScheduler(services.launcher.run_scheduled, db_path=db)

        def sync() -> None:
            snap = services.runs.config()
            scheduler.sync(snap.data.get("schedules", []), snap.flows())

        services.on_config_change = sync
        snap = cfg.snapshot()
        scheduler.start(snap.data.get("schedules", []), snap.flows())
    threading.Thread(target=services.runs.resume_incomplete, name="pkpipe-resume", daemon=True).start()

    def shutdown() -> None:
        services.launcher.shutdown(wait=False)
        if scheduler:
            scheduler.shutdown(wait=False)
        tel.shutdown()

    hosts, public_url = ui_settings(cfg.data, tuple(args.allow_host))
    web = create_app(
        services,
        tokens,
        bind_host=host,
        extra_hosts=hosts,
        public_url=public_url,
        on_shutdown=shutdown,
    )
    tel.instrument(langchain=False, httpx=False, fastapi_app=web)  # request spans (constitution X)
    print(f"pkpipe UI    http://{host}:{args.port}/   (log in with the owner token)")
    if public_url:
        print(f"public URL   {public_url}   (Host names accepted: {', '.join(hosts)})")
    print(f"app MCP      http://{host}:{args.port}/mcp  (Authorization: Bearer <owner or agent token>)")
    print(f"tokens       {db.parent / 'tokens.json'} (or PKPIPE_TOKEN / PKPIPE_AGENT_TOKEN)")
    if services.vault is None:
        print(
            f"note: no vault configured ({VAULT_ENV} / --vault): triage views are disabled", file=sys.stderr
        )
    if not services.workspace.editable:
        print("note: no overlay configured: the config UI is read-only", file=sys.stderr)
    uvicorn.run(web, host=host, port=args.port, log_level="info", server_header=False)
    return 0
