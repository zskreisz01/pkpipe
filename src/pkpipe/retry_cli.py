"""``pkpipe retry [--all | ITEM ...]``: the owner's retry of failed items (spec 027).

Without ``--all`` or item ids it only lists the failed items and the retries each one has used (a dry run).
The terminal is the owner's channel; the action is audited as actor ``cli``. Only item ids and categories are
printed, never a resource or a note text."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pkpipe.engine.registry import ConfigError


def add_retry_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("items", nargs="*", help="item ids to retry (default: only list the failed items)")
    p.add_argument("--all", action="store_true", help="retry every failed item that may still be retried")
    p.add_argument("--pack", help="pack directory (default: packs/learning-inbox or PKPIPE_PACK_DIR)")
    p.add_argument("--overlay", help="overlay directory (default: PKPIPE_OVERLAY_DIR)")
    p.add_argument("--db", help="app DB (default: PKPIPE_DB or $PKPIPE_DATA_DIR/pkpipe.db)")
    p.add_argument("--vault", help="vault directory (default: PKPIPE_VAULT_DIR)")
    p.add_argument("--json", action="store_true", help="machine readable output")


def retry_command(args: argparse.Namespace) -> int:
    from pkpipe.api.services import Refused
    from pkpipe.serve import build_services

    try:
        services = build_services(
            Path(args.pack) if args.pack else None,
            Path(args.overlay) if args.overlay else None,
            Path(args.db) if args.db else None,
            Path(args.vault).expanduser() if args.vault else None,
        )
        failed = services.failed_summary()
        rows = [r for r in services.list_triage("processed")["rows"] if r.get("failed")]
        if args.all and args.items:
            print("error: give item ids or --all, not both", file=sys.stderr)
            return 2
        if args.all or args.items:
            result = services.retry_failed(actor="cli", item_ids=list(args.items) if args.items else None)
            if args.json:
                print(json.dumps(result))
            else:
                print(
                    f"{result['retried']} item(s) go through the next release run; "
                    f"{result['capped']} reached the retry limit, {result['skipped']} skipped"
                )
            return 0
        if args.json:
            print(
                json.dumps(
                    {**failed, "items": [{k: r[k] for k in ("item_id", "category", "retries")} for r in rows]}
                )
            )
        else:
            for r in rows:
                print(
                    f"{r['item_id']}  {r['category']:<18} retried {r['retries']}x"
                    + ("" if r["retryable"] else "  (limit)")
                )
            print(
                f"{failed['failed']} failed, {failed['retryable']} can be retried "
                f"(limit {failed['max']} per item)"
            )
        return 0
    except ConfigError as exc:
        for err in exc.errors:
            print(f"error: {err}", file=sys.stderr)
        return 1
    except Refused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
