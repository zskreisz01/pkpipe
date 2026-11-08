"""``pkpipe item [ITEM_ID]``: the history of one item in time order (spec 030).

Without an id it lists the items with the most recent events. Only ids, codes, numbers and the fixed sentences
of the timeline are printed, never a page, note or resource text."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pkpipe.engine.registry import ConfigError


def add_item_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("item", nargs="?", help="item id (default: list the items with recent events)")
    p.add_argument("--limit", type=int, default=30, help="how many recent items to list (default 30)")
    p.add_argument("--pack", help="pack directory (default: packs/learning-inbox or PKPIPE_PACK_DIR)")
    p.add_argument("--overlay", help="overlay directory (default: PKPIPE_OVERLAY_DIR)")
    p.add_argument("--db", help="app DB (default: PKPIPE_DB or $PKPIPE_DATA_DIR/pkpipe.db)")
    p.add_argument("--vault", help="vault directory (default: PKPIPE_VAULT_DIR)")
    p.add_argument("--json", action="store_true", help="machine readable output")


def item_command(args: argparse.Namespace) -> int:
    from pkpipe.api.services import NotFound
    from pkpipe.serve import build_services

    try:
        services = build_services(
            Path(args.pack) if args.pack else None,
            Path(args.overlay) if args.overlay else None,
            Path(args.db) if args.db else None,
            Path(args.vault).expanduser() if args.vault else None,
        )
        if not args.item:
            ids = services.recent_items(args.limit)
            print(json.dumps(ids) if args.json else "\n".join(ids) or "no item events yet")
            return 0
        h = services.item_history(args.item)
    except ConfigError as exc:
        for err in exc.errors:
            print(f"error: {err}", file=sys.stderr)
        return 1
    except NotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(h, default=str))
        return 0
    row = h["row"] or {}
    state, category = row.get("state", "?"), row.get("category", "?")
    print(f"{h['item_id']}  state={state}  category={category}  retries={h['retries']}")
    for ev in h["timeline"]:
        run = f" [{str(ev['run_id'])[:8]}]" if ev.get("run_id") else ""
        print(f"{str(ev['at'])[:19]}{run}  {ev['text']}")
    return 0
