"""``pkpipe doctor``: the system snapshot and the preflight decision; exit 1 when blocked
(docs/operations.md)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from pkpipe.domain.system import PreflightDecision, SystemSnapshot
from pkpipe.engine.registry import ConfigError, load_effective
from pkpipe.ports import SystemProbePort

_GiB = 1024**3
_MiB = 1024**2

#: Builds the probe for ``doctor`` (tests replace it to force a state): ``base_url -> SystemProbePort``.
probe_factory: Callable[[str | None], SystemProbePort] | None = None


def make_probe(base_url: str | None) -> SystemProbePort:
    if probe_factory is not None:
        return probe_factory(base_url)
    from pkpipe.adapters.system_probe import make_system_probe

    return make_system_probe(base_url)


def _gib(n: int | None) -> str:
    return "?" if n is None else f"{n / _GiB:.1f} GiB"


def format_text(snapshot: SystemSnapshot, decision: PreflightDecision, required: tuple[str, ...]) -> str:
    s, m = snapshot, snapshot.memory
    lines = [f"system      {s.platform}, {s.cpu_count} CPUs"]
    avail = (
        f"{_gib(m.available_bytes)} available ({m.available_pct:g}%)" if m.available_pct is not None else ""
    )
    lines.append(
        f"memory      {_gib(m.total_bytes)} total, {avail or 'available ?'}, pressure {m.pressure or '?'}"
        + (f", compressed {_gib(m.compressed_bytes)}" if m.compressed_bytes is not None else "")
    )
    swap_pct = f" ({s.swap.used_pct:g}%)" if s.swap.used_pct is not None else ""
    lines.append(f"swap        {_gib(s.swap.used_bytes)} of {_gib(s.swap.total_bytes)} used{swap_pct}")
    th = s.thermal
    limit = f", CPU speed limit {th.cpu_speed_limit}%" if th.cpu_speed_limit is not None else ""
    lines.append(f"thermal     {th.level or '?'}{limit}" + (", warning recorded" if th.warning else ""))
    if s.load_avg:
        la = " ".join(f"{x:.2f}" for x in s.load_avg)
        lines.append(f"load        {la} ({s.load_per_cpu:g} per CPU)")
    if s.processes:
        top = ", ".join(f"{p.name} {p.rss_bytes / _MiB:,.0f} MiB" for p in s.processes)
        lines.append(f"top memory  {top}")
    if s.llm is not None:
        state = "reachable" if s.llm.reachable else f"NOT reachable ({s.llm.error})"
        lines.append(f"llm server  {s.llm.base_url} {state} [{', '.join(s.llm.sources)}]")
        for model in s.llm.models:
            if model.loaded or model.id in required:
                need = " (required)" if model.id in required else ""
                loaded = {True: "loaded", False: "not loaded", None: "served"}[model.loaded]
                extra = ", ".join(
                    f"{k} {v}"
                    for k, v in (
                        ("parallel", model.parallel),
                        ("context", model.context_length),
                        ("status", model.status),
                        ("queued", model.queued),
                    )
                    if v is not None
                )
                lines.append(f"  - {model.id}{need}: {loaded}" + (f", {extra}" if extra else ""))
    if s.probe_errors:
        lines.append(f"not read    {', '.join(s.probe_errors)}")
    lines.append("")
    lines.append(
        f"decision    {decision.status.value.upper()}: eval concurrency {decision.concurrency}, "
        f"timeouts x{decision.timeout_multiplier:g}"
    )
    for r in decision.reasons:
        lines.append(f"  - [{r.severity}] {r.code}: {r.message}")
    return "\n".join(lines) + "\n"


def snapshot_section(snapshot) -> tuple[dict, list[str]]:
    """The vault snapshot directory (spec 019 FR-009): free space, number, size and newest snapshot, with a
    warning when the disk cannot hold ``snapshots.max_bytes`` more."""
    from pkpipe import app
    from pkpipe.adapters.snapshot_files import directory_summary

    cfg = snapshot.data.get("snapshots") or {}
    info = directory_summary(
        app.snapshots_dir(None),
        **{k: cfg[k] for k in ("retention_days", "max_bytes") if k in cfg},
    )
    newest = info["newest"]
    lines = [
        f"snapshots   {info['directory']}: {info['snapshots']} kept, {info['bytes'] / _MiB:,.0f} MiB "
        f"(limit {info['max_bytes'] / _MiB:,.0f} MiB, {info['retention_days']} days), "
        f"{_gib(info['free_bytes'])} free"
        + (
            f", newest {newest['run_id'][:8]} ({newest['flow_id']}) {newest['taken_at'][:19]}"
            if newest
            else ""
        )
    ]
    if info["free_bytes"] < info["max_bytes"]:
        lines.append("  - [warn] snapshot_disk_low: less free disk than snapshots.max_bytes")
    return info, lines


def doctor_command(args: argparse.Namespace) -> int:
    from pkpipe import app
    from pkpipe.system.preflight import model_targets, preflight_settings, run_preflight

    try:
        cfg = load_effective(
            Path(args.pack) if args.pack else None, Path(args.overlay) if args.overlay else None
        )
    except ConfigError as exc:
        for err in exc.errors:
            print(f"error: {err}", file=sys.stderr)
        return 2
    snapshot = cfg.snapshot()
    settings = preflight_settings(snapshot)
    base_url, required = model_targets(snapshot)
    if args.base_url:
        base_url = args.base_url
    if args.model:
        required = tuple(args.model)
    tel = app.configure_telemetry(snapshot)
    try:
        pre = run_preflight(
            make_probe(None if args.no_llm else base_url),
            settings,
            required_models=() if args.no_llm else required,
            max_concurrency=args.max_concurrency,
            telemetry=tel,
            include_llm=not args.no_llm,
        )
    finally:
        tel.shutdown()
    snap_info, snap_lines = snapshot_section(snapshot)
    if args.json:
        print(json.dumps({**pre.to_dict(), "snapshots": snap_info}, indent=2))
    else:
        print(format_text(pre.snapshot, pre.decision, pre.required_models), end="")
        print("\n".join(snap_lines))
    return 1 if pre.decision.blocked else 0


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--pack", help="pack directory (default: packs/learning-inbox or PKPIPE_PACK_DIR)")
    p.add_argument("--overlay", help="overlay directory (default: PKPIPE_OVERLAY_DIR)")
    p.add_argument("--base-url", help="LLM server (default: the `default` model profile)")
    p.add_argument(
        "--model",
        action="append",
        help="required model id (repeatable; default: model under test and eval judge from the config)",
    )
    p.add_argument(
        "--max-concurrency",
        type=int,
        help="concurrency when all is ok (default: evals.preflight.max_concurrency)",
    )
    p.add_argument("--no-llm", action="store_true", help="host only, do not contact the LLM server")
    p.add_argument("--json", action="store_true", help="machine readable output")


__all__ = ["add_arguments", "doctor_command", "format_text", "make_probe", "snapshot_section"]
