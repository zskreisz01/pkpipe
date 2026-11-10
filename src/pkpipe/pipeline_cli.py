"""``pkpipe pipeline``: the pipeline overview in the terminal (spec 033).

The same overview the web page and ``Pipeline.md`` show: the funnel, what needs the owner, what waits, what
was closed lately. Only ids, codes, URLs and the short fixed sentences are printed, never a page or note
text."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pkpipe.engine.registry import ConfigError


def add_pipeline_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--pack", help="pack directory (default: packs/learning-inbox or PKPIPE_PACK_DIR)")
    p.add_argument("--overlay", help="overlay directory (default: PKPIPE_OVERLAY_DIR)")
    p.add_argument("--db", help="app DB (default: PKPIPE_DB or $PKPIPE_DATA_DIR/pkpipe.db)")
    p.add_argument("--vault", help="vault directory (default: PKPIPE_VAULT_DIR)")
    p.add_argument("--json", action="store_true", help="machine readable output")


def render_text(ov: dict) -> str:
    f = ov["funnel"]
    lines = []
    cap = f"{f['captured']} captured, not yet triaged -> " if f.get("captured") is not None else ""
    lines.append(cap + " -> ".join(f"{s['label'].lower()} {s['count']}" for s in f["stages"]))
    for stage in f["stages"]:
        for b in stage["buckets"]:
            if b["count"]:
                lines.append(f"  {stage['label']:<10} {b['count']:>4}  {b['label']}")
    needs = ov["needs_you"]
    lines.append(f"\nNeeds you ({len(needs)})")
    for it in needs:
        lines.append(f"  {it['item_id']}  {it['label']} [{it['reason']}]  next: {it['next_step']}")
    w = ov["waiting"]
    lines.append(
        f"\nWaiting: {len(w['released'])} released for the next run, {len(w['waiting_quota'])} waiting for "
        f"NotebookLM quota, {len(w['generating'])} being generated"
    )
    for n in w["next_runs"]:
        lines.append(f"  next {n['flow']}: {n['at']} UTC")
    lines.append(f"\nDone in the last {ov['recent_days']} days: {len(ov['done_recent'])}")
    for r in ov["runs"]:
        lines.append(f"  last {r['flow']}: {r['status']} {r['started']} UTC, {r['items']} item(s)")
    return "\n".join(lines)


def pipeline_command(args: argparse.Namespace) -> int:
    from pkpipe.api.services import Refused
    from pkpipe.serve import build_services

    try:
        services = build_services(
            Path(args.pack) if args.pack else None,
            Path(args.overlay) if args.overlay else None,
            Path(args.db) if args.db else None,
            Path(args.vault).expanduser() if args.vault else None,
        )
        ov = services.pipeline_overview()
    except ConfigError as exc:
        for err in exc.errors:
            print(f"error: {err}", file=sys.stderr)
        return 1
    except Refused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(ov, default=str) if args.json else render_text(ov))
    return 0
