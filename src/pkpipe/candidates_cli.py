"""``pkpipe candidates ...`` (spec 016): the owner's side of the system tool candidates.

* ``list``  the candidate list of the vault with the rules its rows break;
* ``ab``    spike evidence: the effective config against a candidate config (a second pack and/or overlay with
            the tool behind its adapter) on the eval suites, written as a JSON report; exit 0 only when the
            candidate is ``better``;
* ``adr``   write the ADR of an adoption from the report (template: ``docs/decisions/template.md``);
* ``set``   change a candidate's status. Adoption needs the ADR file and an A/B report that says ``better``
            with a green gate; a rejection needs ``--reason``; a rejected tool returns only with
            ``--evidence``.

Status changes are the owner's (constitution VI): they run in-process through
:mod:`pkpipe.obsidian.tool_candidates`, not through a tool of the vault server a session could call.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from pkpipe.domain import tool_candidate as tc
from pkpipe.obsidian import tool_candidates as store
from pkpipe.obsidian.policy import VaultPolicy

VAULT_ENV = "PKPIPE_VAULT_DIR"


def _candidates_path(args: argparse.Namespace) -> Path:
    vault = args.vault or os.environ.get(VAULT_ENV)
    if not vault:
        raise SystemExit("error: no vault (--vault or PKPIPE_VAULT_DIR)")
    policy = VaultPolicy(vault_dir=Path(vault))
    return policy.root / policy.tool_candidates_file


def _row(path: Path, tool: str) -> tc.ToolCandidate | None:
    for r in store.read_tool_candidates(path)["rows"]:
        if r["id"] == tc.candidate_id(tool):
            return tc.ToolCandidate.model_validate(r)
    return None


def _load_report(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"error: cannot read the A/B report {path}: {exc}") from None
    if not isinstance(data, dict) or "verdict" not in data:
        raise SystemExit(f"error: {path} is not an A/B report")
    return data


def _adr_exists(adr: str, adr_dir: Path) -> bool:
    if not adr:
        return False
    name = Path(adr).name
    return name.endswith(".md") and (adr_dir / name).is_file()


def _list(args: argparse.Namespace) -> int:
    data = store.read_tool_candidates(_candidates_path(args))
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    for r in data["rows"]:
        print(f"{r['status']:<13} {r['tool']:<28} {r['component']:<24} effort {r['effort']}  {r['benefit']}")
    for v in data["violations"]:
        print(f"violation: {v['id']}: {v['problem']}", file=sys.stderr)
    return 1 if data["violations"] else 0


def _set(args: argparse.Namespace) -> int:
    path = _candidates_path(args)
    row = _row(path, args.tool)
    if row is None:
        print(f"error: no candidate '{args.tool}' in the list", file=sys.stderr)
        return 1
    status = tc.Status(args.status)
    report = _load_report(args.ab_report)
    ab = None
    if report is not None:
        ab = tc.AbEvidence(
            tool=report.get("tool") or row.tool,
            verdict=str(report["verdict"]),
            gate_green=bool(report.get("gate_green")),
            summary=_summary(report),
        )
    adr = args.adr or row.adr
    adr_dir = Path(args.adr_dir)
    try:
        result = store.owner_set_status(
            path,
            row.id,
            status,
            reason=args.reason or "",
            evidence=args.evidence or "",
            adr=adr,
            adr_exists=_adr_exists(adr, adr_dir),
            ab=ab,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{row.tool}: {result['old']} -> {result['new']}")
    return 0


def _summary(report: dict) -> str:
    from pkpipe.evals.ab import AbReport

    try:
        return AbReport.model_validate(report).summary()
    except ValueError:
        return str(report.get("verdict"))


def _adr(args: argparse.Namespace) -> int:
    path = _candidates_path(args)
    row = _row(path, args.tool)
    report = _load_report(args.ab_report)
    if row is None or report is None:
        print("error: needs a listed candidate and --ab-report", file=sys.stderr)
        return 1
    adr_dir = Path(args.adr_dir)
    adr_dir.mkdir(parents=True, exist_ok=True)
    number = tc.next_adr_number([p.name for p in adr_dir.glob("*.md")])
    target = adr_dir / tc.adr_filename(number, row.tool)
    target.write_text(
        tc.render_adr(number=number, row=row, today=date.today().isoformat(), ab=report), encoding="utf-8"
    )
    print(target)
    return 0


def _ab(args: argparse.Namespace) -> int:
    from pkpipe import app
    from pkpipe.engine.registry import ConfigError, load_effective
    from pkpipe.evals.ab import compare
    from pkpipe.evals.datasets import EvalPaths
    from pkpipe.evals.suites import resolve_suites

    try:
        names = resolve_suites(args.suite) if args.suite else None
        base = load_effective(
            Path(args.pack) if args.pack else None, Path(args.overlay) if args.overlay else None
        )
        cand = load_effective(
            Path(args.candidate_pack) if args.candidate_pack else (Path(args.pack) if args.pack else None),
            Path(args.candidate_overlay) if args.candidate_overlay else None,
        )
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        for err in exc.errors:
            print(f"error: {err}", file=sys.stderr)
        return 1
    mode = "live" if args.cassette == "off" else args.cassette
    paths = EvalPaths.create(
        Path(args.evals_dir) if args.evals_dir else None,
        cand.overlay_dir or base.overlay_dir,
        Path(args.cassette_dir) if args.cassette_dir else None,
    )
    snapshot = base.snapshot()
    tel = app.configure_telemetry(snapshot)
    try:
        model = None if mode == "replay" else app.make_model(snapshot)
        report = compare(
            snapshot,
            cand.snapshot(),
            paths=paths,
            model=model,
            suites=names,
            mode=mode,
            concurrency=args.concurrency,
            limit=args.limit,
            tool=args.tool,
        )
    finally:
        tel.shutdown()
    out = Path(args.out) if args.out else Path(f"ab-{tc.candidate_id(args.tool)}.json")
    out.write_text(report.to_json(), encoding="utf-8")
    print(f"{args.tool}: {report.summary()}")
    for reason in report.reasons:
        print(f"  {reason}")
    print(f"report: {out}", file=sys.stderr)
    return 0 if report.verdict == "better" else 1


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="candidates_cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vault", help="vault directory (default: PKPIPE_VAULT_DIR)")
    common.add_argument("--adr-dir", default=tc.ADR_DIR, help=f"ADR directory (default: {tc.ADR_DIR})")
    ls = sub.add_parser("list", parents=[common], help="show the candidate list")
    ls.add_argument("--json", action="store_true")
    ab = sub.add_parser(
        "ab", help="A/B of the effective config against a candidate config on the eval suites"
    )
    ab.add_argument("tool")
    ab.add_argument("--pack", help="baseline pack directory (default: packs/learning-inbox)")
    ab.add_argument("--overlay", help="baseline overlay directory")
    ab.add_argument("--candidate-pack", help="candidate pack directory (default: the baseline pack)")
    ab.add_argument("--candidate-overlay", help="candidate overlay directory (the spike's configuration)")
    ab.add_argument("--suite", help="suites to compare, comma list (default: sessions,triage,flows,security)")
    ab.add_argument("--evals-dir")
    ab.add_argument("--cassette", default="off", choices=["off", "replay", "record", "auto"])
    ab.add_argument("--cassette-dir")
    ab.add_argument("--concurrency", type=int)
    ab.add_argument("--limit", type=int)
    ab.add_argument("--out", help="report file (default: ab-<tool>.json)")
    st = sub.add_parser("set", parents=[common], help="change the status of a candidate (the owner decides)")
    st.add_argument("tool")
    st.add_argument("--status", required=True, choices=[s.value for s in tc.Status])
    st.add_argument("--reason", help="required for rejected")
    st.add_argument("--evidence", help="required to reopen a rejected candidate")
    st.add_argument("--adr", help="ADR file name in the ADR directory (required for adopted)")
    st.add_argument("--ab-report", help="A/B report of `pkpipe candidates ab` (required for adopted)")
    adr = sub.add_parser("adr", parents=[common], help="write the ADR of an adoption from an A/B report")
    adr.add_argument("tool")
    adr.add_argument("--ab-report", required=True)


def candidates_command(args: argparse.Namespace) -> int:
    return {"list": _list, "ab": _ab, "set": _set, "adr": _adr}[args.candidates_cmd](args)
