"""Command line entry point: ``serve``, ``run <flow>``, ``eval --suite``, ``validate`` (spec 001, FR-003).

``validate`` is real; the other commands are registered here and wired by later specs (engine, API, evals).
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
    schemas = {**config_json_schemas(), **vault_json_schemas()}
    for name, schema in schemas.items():
        (out / f"{name}.schema.json").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(schemas)} schemas to {out}")
    return 0


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"'{args.cmd}' is not implemented yet", file=sys.stderr)
    return 1


#: command name -> handler. Later specs replace the stubs (``HANDLERS["serve"] = ...``).
HANDLERS: dict[str, Handler] = {
    "serve": _not_implemented,
    "run": _not_implemented,
    "eval": _not_implemented,
    "validate": _validate,
    "schema": _schema,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pkpipe", description="Local-first agent pipeline")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="start API, UI, scheduler and app MCP server")
    run = sub.add_parser("run", help="run one flow now")
    run.add_argument("flow")
    ev = sub.add_parser("eval", help="run an eval suite")
    ev.add_argument("--suite", default="all")
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
