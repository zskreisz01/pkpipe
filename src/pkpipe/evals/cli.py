"""``pkpipe eval --suite ...`` (spec 013 US1): run suites, write JSON + Markdown reports; exit 1 on a red
gate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pkpipe.engine.registry import ConfigError, load_effective


def default_report_dir() -> Path:
    from pkpipe.app import default_db_path

    return default_db_path().parent / "evals"


def eval_command(args: argparse.Namespace) -> int:
    from pkpipe import app
    from pkpipe.evals.datasets import EvalPaths
    from pkpipe.evals.report import to_json, to_markdown, write_reports
    from pkpipe.evals.runner import EvalRunner
    from pkpipe.evals.suites import resolve_suites

    try:
        names = resolve_suites(args.suite)
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2
    try:
        cfg = load_effective(
            Path(args.pack) if args.pack else None, Path(args.overlay) if args.overlay else None
        )
    except ConfigError as exc:
        for err in exc.errors:
            print(f"error: {err}", file=sys.stderr)
        return 1
    snapshot = cfg.snapshot()
    mode = "live" if args.cassette == "off" else args.cassette
    paths = EvalPaths.create(
        Path(args.evals_dir) if args.evals_dir else None,
        cfg.overlay_dir,
        Path(args.cassette_dir) if args.cassette_dir else None,
    )
    tel = app.configure_telemetry(snapshot)
    try:
        concurrency, multiplier = args.concurrency, None
        if mode != "replay" and args.concurrency is None and not args.no_preflight:
            pre = _preflight(snapshot, names, tel)
            if pre is not None:
                print(f"preflight: {pre.decision.summary()}", file=sys.stderr)
                if pre.decision.blocking("system"):
                    print(
                        "preflight: blocked (pkpipe doctor shows why; --no-preflight runs anyway)",
                        file=sys.stderr,
                    )
                    return 1
                if not pre.decision.blocked:  # an unreachable model is reported by the suites themselves
                    concurrency, multiplier = pre.decision.concurrency, pre.decision.timeout_multiplier
        # replay never touches the network
        model = None if mode == "replay" else app.make_model(snapshot, timeout_multiplier=multiplier)
        runner = EvalRunner(
            snapshot,
            paths=paths,
            model=model,
            mode=mode,
            concurrency=concurrency,
            limit=args.limit,
            options={"db": args.db, "vault": args.vault, "fetch_stats": args.fetch_stats},
        )
        run = runner.run(names)
    finally:
        tel.shutdown()
    out = Path(args.out) if args.out else default_report_dir()
    json_path, md_path = write_reports(run, out)
    if args.json:
        print(to_json(run), end="")
    else:
        print(to_markdown(run), end="")
        print(f"\nreports: {json_path} {md_path}", file=sys.stderr)
    if not run.ok:
        for failure in run.failures():
            print(f"gate: {failure}", file=sys.stderr)
    return 0 if run.ok else 1


def _preflight(snapshot, names: list[str], tel):
    """The system preflight before suites that call a model (docs/operations.md): its decision caps
    ``evals.concurrency`` (production default 1, so it only ever lowers it) and scales model timeouts."""
    from pkpipe.evals.runner import evals_config
    from pkpipe.evals.suites import SUITES
    from pkpipe.system.cli import make_probe
    from pkpipe.system.preflight import model_targets, run_preflight

    config = evals_config(snapshot)
    if not config.preflight.enabled or not any(SUITES[n].uses_model or SUITES[n].uses_judge for n in names):
        return None
    base_url, required = model_targets(snapshot)
    return run_preflight(
        make_probe(base_url),
        config.preflight,
        required_models=required,
        max_concurrency=config.concurrency,
        telemetry=tel,
    )


def _positive(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return n


def add_arguments(ev: argparse.ArgumentParser) -> None:
    ev.add_argument("--suite", default="all", help="suite name, comma list, or 'all' (default)")
    ev.add_argument("--pack", help="pack directory (default: packs/learning-inbox or PKPIPE_PACK_DIR)")
    ev.add_argument("--overlay", help="overlay directory (default: PKPIPE_OVERLAY_DIR); <overlay>/evals wins")
    ev.add_argument("--evals-dir", help="public evals directory (default: evals/ or PKPIPE_EVALS_DIR)")
    ev.add_argument(
        "--cassette",
        default="off",
        choices=["off", "replay", "record", "auto"],
        help="off = live model; replay = recorded responses only (CI, no network); record = call the "
        "model and (re)write cassettes; auto = replay what exists, record the rest",
    )
    ev.add_argument("--cassette-dir", help="cassette directory (default: <evals>/cassettes)")
    ev.add_argument(
        "--concurrency",
        "--workers",
        dest="concurrency",
        type=_positive,
        help="eval cases (model + judge requests) in flight at once; default evals.concurrency (1). Results "
        "and metrics are the same for every value; > 1 needs parallel predictions in LM Studio. Without it "
        "the system preflight (pkpipe doctor) may lower the default and lengthen model timeouts",
    )
    ev.add_argument(
        "--no-preflight",
        action="store_true",
        help="skip the system preflight (memory, swap, thermal, load, LLM server) before model suites",
    )
    ev.add_argument("--limit", type=int, help="only the first N cases per dataset (smoke run)")
    ev.add_argument("--out", help="report directory (default: $PKPIPE_DATA_DIR/evals)")
    ev.add_argument("--db", help="production suite: app DB to read runs from")
    ev.add_argument("--vault", help="production suite: vault directory (triage file, processing list)")
    ev.add_argument(
        "--fetch-stats", help="production suite: web-fetch stats file (fallback rate, spec 008 SC-002)"
    )
    ev.add_argument("--json", action="store_true", help="print the JSON report instead of Markdown")


__all__ = ["add_arguments", "eval_command"]
