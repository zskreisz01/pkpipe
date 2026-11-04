"""``pkpipe snapshot list|show|prune`` and ``pkpipe restore RUN [--apply] [--force]`` (spec 019 FR-004).

``restore`` is a dry run unless ``--apply``. The terminal is the owner's: it shows note paths (the plan has
to name what it moves); spans, audit entries and notifications never do."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pkpipe.domain.vault_snapshot import RestorePlan, SnapshotError
from pkpipe.engine.registry import ConfigError, load_effective
from pkpipe.engine.restore import NOT_COVERED, resolve_run_id, restore_run
from pkpipe.engine.runs import Run
from pkpipe.ports import SnapshotPort

NO_VAULT = "error: no vault (--vault or PKPIPE_VAULT_DIR): the snapshots cannot be reached"


def add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--pack", help="pack directory (default: packs/learning-inbox or PKPIPE_PACK_DIR)")
    p.add_argument("--overlay", help="overlay directory (default: PKPIPE_OVERLAY_DIR)")
    p.add_argument("--db", help="app DB (default: PKPIPE_DB or $PKPIPE_DATA_DIR/pkpipe.db)")
    p.add_argument("--vault", help="vault directory (default: PKPIPE_VAULT_DIR)")
    p.add_argument("--json", action="store_true", help="machine readable output")


def add_snapshot_arguments(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="snapshot_cmd", required=True)
    for name, help_ in (
        ("list", "snapshots: run, flow, time, size, files, status"),
        ("show", "one snapshot: scope and what the run changed"),
        ("prune", "remove snapshots past the retention (the newest per flow stays)"),
    ):
        sp = sub.add_parser(name, help=help_)
        if name == "show":
            sp.add_argument("run", help="run id (or a unique prefix of at least 6 characters)")
        add_common(sp)


def add_restore_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("run", help="run id (or a unique prefix of at least 6 characters)")
    p.add_argument("--apply", action="store_true", help="do it (default: print the plan, change nothing)")
    p.add_argument(
        "--force",
        action="store_true",
        help="also overwrite what changed since the run, after saving the current bytes in the trash folder",
    )
    add_common(p)


class RunTable:
    """The run table of the app DB, read only: an active run is refused by the restore."""

    def __init__(self, db: Path):
        self.db = db

    def get(self, run_id: str) -> Run | None:
        from pkpipe.adapters.state_sqlite import SqliteRunStore

        rec = SqliteRunStore(self.db).get_run(run_id)
        return Run.model_validate(rec) if rec else None


def _open(args: argparse.Namespace) -> tuple[SnapshotPort | None, Path]:
    from pkpipe import app

    pack = Path(args.pack) if args.pack else None
    overlay = Path(args.overlay) if args.overlay else None
    cfg = load_effective(pack, overlay)
    db = Path(args.db) if args.db else app.default_db_path()
    vault = Path(args.vault).expanduser() if args.vault else None
    return app.make_snapshots(cfg.snapshot(), db, vault), db


def _fmt_size(n: int) -> str:
    return f"{n / 1024:.0f} KiB" if n < 1024**2 else f"{n / 1024**2:.1f} MiB"


def format_plan(plan: RestorePlan, *, force: bool) -> str:
    head = f"restore of run {plan.run_id}: " + (
        "applied" if plan.applied else "dry run, nothing changed (add --apply to do it)"
    )
    lines = [head]
    if not plan.steps:
        lines.append("  nothing to do: the vault already has the state from before the run")
    for step in plan.steps:
        what = f"{step.from_path} -> {step.to}" if step.verb == "move_back" else step.path
        note = f"  ({step.reason})" if step.reason else ""
        outcome = f"  => {step.outcome}" if plan.applied and step.outcome else ""
        lines.append(f"  {step.verb:<9} {what}{note}{outcome}")
    conflicts = plan.conflicts
    if conflicts and not force:
        lines.append(
            f"{len(conflicts)} file(s) skipped; --force overwrites them after saving their current bytes"
        )
    if plan.safety_snapshot:
        lines.append(
            f"state before the restore saved as snapshot {plan.safety_snapshot} (restore it to undo)"
        )
    if plan.applied and plan.steps:
        lines.append(NOT_COVERED)
    return "\n".join(lines)


def snapshot_command(args: argparse.Namespace) -> int:
    try:
        snapshots, _db = _open(args)
    except ConfigError as exc:
        for err in exc.errors:
            print(f"error: {err}", file=sys.stderr)
        return 1
    if snapshots is None:
        print(NO_VAULT, file=sys.stderr)
        return 1
    try:
        if args.snapshot_cmd == "list":
            infos = snapshots.list()
            if args.json:
                print(json.dumps([i.model_dump(mode="json") for i in infos]))
            else:
                for i in infos:
                    flag = " reverted" if i.reverted else ""
                    done = "" if i.finished or i.status == "pruned" else " (unfinished)"
                    print(
                        f"{i.run_id}  {i.flow_id:<20} {i.taken_at[:19]}  {_fmt_size(i.bytes):>9}  "
                        f"{i.files:>5} files  {i.status}{flag}{done}"
                    )
                if not infos:
                    print("no snapshots")
        elif args.snapshot_cmd == "show":
            run_id = resolve_run_id(snapshots.list(), args.run)
            data = snapshots.show(run_id)
            if args.json:
                print(json.dumps(data))
            else:
                i = data["info"]
                print(
                    f"run {i['run_id']}  flow {data['flow_id']}  taken {i['taken_at']}  status {i['status']}"
                )
                print(
                    f"scope   {', '.join(data['scope'])}  (excluded: {', '.join(data['exclude']) or 'none'})"
                )
                print(
                    f"policy  {data['policy_digest'][:12]}  files {i['files']}  bytes {_fmt_size(i['bytes'])}"
                )
                for path in data["not_copied"]:
                    print(f"  not copied: {path}")
                if data["changes"] is None:
                    print("the run has not finished: no post manifest yet")
                for change in data["changes"] or []:
                    src = f" (from {change['from']})" if change.get("from") else ""
                    print(f"  {change['state']:<10} {change['path']}{src}")
        else:
            result = snapshots.prune()
            if args.json:
                print(json.dumps(result))
            else:
                print(
                    f"pruned {len(result['pruned'])} snapshot(s), freed {_fmt_size(result['freed_bytes'])}, "
                    f"kept {result['kept']}"
                )
    except SnapshotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def restore_command(args: argparse.Namespace) -> int:
    from pkpipe.adapters.app_store_sqlite import AppStore

    try:
        snapshots, db = _open(args)
    except ConfigError as exc:
        for err in exc.errors:
            print(f"error: {err}", file=sys.stderr)
        return 1
    if snapshots is None:
        print(NO_VAULT, file=sys.stderr)
        return 1
    store = AppStore(db)
    try:
        plan = restore_run(snapshots, args.run, apply=args.apply, force=args.force, runs=RunTable(db))
    except SnapshotError as exc:
        if args.apply:
            store.audit("cli", "restore_run_refused", args.run, reason=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.apply:
        store.audit(
            "cli", "restore_run", plan.run_id, force=args.force, **plan.counts(), safety=plan.safety_snapshot
        )
    if args.json:
        print(json.dumps(plan.to_json()))
    else:
        print(format_plan(plan, force=args.force))
    unfinished = plan.applied and any(s.outcome not in ("done", None) for s in plan.steps)
    return 2 if unfinished else 0
