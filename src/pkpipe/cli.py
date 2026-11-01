"""Command line entry point: ``serve``, ``run <flow>``, ``runs``, ``eval --suite``, ``validate`` (spec 001).

``validate``, ``schema``, ``run``, ``runs`` (spec 005), ``eval`` (spec 013) and ``serve`` (spec 014) are real.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from pkpipe import __version__
from pkpipe.engine.config_models import config_json_schemas
from pkpipe.engine.registry import ConfigError, load_effective
from pkpipe.obsidian.models import vault_json_schemas
from pkpipe.security.guard import guard_json_schemas
from pkpipe.security.verdict import security_json_schemas

Handler = Callable[[argparse.Namespace], int]


def _validate(args: argparse.Namespace) -> int:
    pack = Path(args.pack) if args.pack else None
    overlay = Path(args.overlay) if args.overlay else None
    try:
        cfg = load_effective(pack, overlay)
    except ConfigError as exc:
        if args.json:
            print(json.dumps({"ok": False, "errors": exc.errors}))
        else:
            for err in exc.errors:
                print(f"error: {err}", file=sys.stderr)
            print(f"invalid config ({len(exc.errors)} error(s))", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True, "version": cfg.version, "warnings": cfg.warnings}))
    else:
        for w in cfg.warnings:
            print(f"warning: {w}", file=sys.stderr)
        print(f"config ok (version {cfg.version[:12]})")
    return 0


def _schema(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    schemas = {
        **config_json_schemas(),
        **vault_json_schemas(),
        **security_json_schemas(),
        **guard_json_schemas(),
    }
    for name, schema in schemas.items():
        (out / f"{name}.schema.json").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(schemas)} schemas to {out}")
    return 0


def _print_run(outcome, as_json: bool) -> None:
    run = outcome.run
    if as_json:
        payload = {**run.record(), "outputs": outcome.outputs if outcome.state is not None else None}
        print(json.dumps(payload, default=str))
        return
    print(f"run {run.run_id} flow {run.flow_id} {run.status.value} (config {run.config_version[:12]})")
    if run.error:
        print(f"error: {run.error}", file=sys.stderr)


def _run(args: argparse.Namespace) -> int:
    """``pkpipe run <flow>`` starts a run; ``--resume ID`` / ``--resume-all`` continue interrupted runs."""
    from pkpipe import app

    if not args.flow and not args.resume and not args.resume_all:
        print("error: name a flow, or use --resume RUN_ID / --resume-all", file=sys.stderr)
        return 2
    pack = Path(args.pack) if args.pack else None
    overlay = Path(args.overlay) if args.overlay else None
    try:
        cfg = load_effective(pack, overlay)
    except ConfigError as exc:
        for err in exc.errors:
            print(f"error: {err}", file=sys.stderr)
        return 1
    try:
        inputs = json.loads(args.input) if args.input else {}
    except json.JSONDecodeError as exc:
        print(f"error: --input is not JSON ({exc.msg})", file=sys.stderr)
        return 2
    tel = app.configure_telemetry(cfg.snapshot())
    service = app.build_run_service(pack, overlay, Path(args.db) if args.db else None)
    try:
        if args.resume_all:
            outcomes = service.resume_incomplete()
        elif args.resume:
            outcomes = [service.resume(args.resume)]
        else:
            outcomes = [service.start(args.flow, trigger=args.trigger, inputs=inputs)]
    except (KeyError, ValueError, LookupError) as exc:
        print(f"error: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        return 1
    finally:
        tel.shutdown()
    for outcome in outcomes:
        _print_run(outcome, args.json)
    return 0 if all(o.ok for o in outcomes) else 1


def _runs(args: argparse.Namespace) -> int:
    from pkpipe.adapters.state_sqlite import SqliteRunStore
    from pkpipe.app import default_db_path

    store = SqliteRunStore(Path(args.db) if args.db else default_db_path())
    rows = store.list_runs(status=args.status, flow_id=args.flow)
    if args.json:
        print(json.dumps(rows))
    else:
        for r in rows:
            print(f"{r['run_id']}  {r['flow_id']:<20} {r['status']:<8} {r['trigger']:<8} {r['started_at']}")
    return 0


def _eval(args: argparse.Namespace) -> int:
    from pkpipe.evals.cli import eval_command

    return eval_command(args)


def _serve(args: argparse.Namespace) -> int:
    from pkpipe.serve import serve_command

    return serve_command(args)


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"'{args.cmd}' is not implemented yet", file=sys.stderr)
    return 1


#: command name -> handler. Later specs replace the stubs (``HANDLERS["serve"] = ...``).
HANDLERS: dict[str, Handler] = {
    "serve": _serve,
    "run": _run,
    "runs": _runs,
    "eval": _eval,
    "validate": _validate,
    "schema": _schema,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pkpipe", description="Local-first agent pipeline")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="start API, UI, scheduler and app MCP server")
    from pkpipe.serve import add_arguments as add_serve_arguments

    add_serve_arguments(serve)
    run = sub.add_parser("run", help="run one flow now, or resume interrupted runs")
    run.add_argument("flow", nargs="?")
    run.add_argument("--resume", metavar="RUN_ID", help="continue a run from its last checkpoint")
    run.add_argument("--resume-all", action="store_true", help="continue every running/waiting run")
    run.add_argument("--input", help="JSON object available to the flow as input.*")
    run.add_argument("--trigger", default="manual", choices=["manual", "schedule", "mcp", "ui"])
    run.add_argument("--pack", help="pack directory (default: packs/learning-inbox or PKPIPE_PACK_DIR)")
    run.add_argument("--overlay", help="overlay directory (default: PKPIPE_OVERLAY_DIR)")
    run.add_argument("--db", help="app DB (default: PKPIPE_DB or $PKPIPE_DATA_DIR/pkpipe.db)")
    run.add_argument("--json", action="store_true", help="machine readable output")
    runs = sub.add_parser("runs", help="list recorded runs")
    runs.add_argument("--status", choices=["running", "waiting", "done", "failed"])
    runs.add_argument("--flow")
    runs.add_argument("--db")
    runs.add_argument("--json", action="store_true")
    ev = sub.add_parser("eval", help="run eval suites and check their gates")
    from pkpipe.evals.cli import add_arguments

    add_arguments(ev)
    val = sub.add_parser("validate", help="validate the layered config (pack + overlay)")
    val.add_argument("--pack", help="pack directory (default: packs/learning-inbox or PKPIPE_PACK_DIR)")
    val.add_argument("--overlay", help="overlay directory (default: PKPIPE_OVERLAY_DIR)")
    val.add_argument("--json", action="store_true", help="machine readable output")
    sch = sub.add_parser("schema", help="export JSON Schemas of all config files")
    sch.add_argument("--out", default="schemas/config")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 0
    return HANDLERS[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
