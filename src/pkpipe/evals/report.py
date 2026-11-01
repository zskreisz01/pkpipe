"""Eval reports as JSON and Markdown (spec 013 FR-004)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pkpipe.evals.models import EvalRun, SuiteReport, _fmt

MAX_FAILED_CASES = 40


def to_json(run: EvalRun) -> str:
    payload = run.model_dump(mode="json")
    payload["ok"] = run.ok
    return json.dumps(payload, indent=1, ensure_ascii=False) + "\n"


def _cell(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = text.replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= 120 else text[:117] + "..."


def _scalar_metrics(metrics: dict[str, Any]) -> list[tuple[str, Any]]:
    return [
        (k, v) for k, v in metrics.items() if isinstance(v, int | float | str) and not isinstance(v, bool)
    ]


def _suite_md(s: SuiteReport) -> list[str]:
    lines = [f"## {s.suite} (level {int(s.level)}): {s.status.value}", ""]
    if s.reason:
        lines += [f"Reason: {s.reason}", ""]
    scalars = _scalar_metrics(s.metrics)
    if scalars:
        lines += ["| Metric | Value |", "| --- | --- |"]
        lines += [f"| {k} | {_fmt(v) if isinstance(v, float) else v} |" for k, v in scalars]
        lines.append("")
    if s.gate:
        lines += ["| Gate | Value | Threshold | Result |", "| --- | --- | --- | --- |"]
        lines += [
            f"| {g.metric} | {_fmt(g.value)} | {g.op} {_fmt(g.threshold)} | "
            f"{'pass' if g.passed else 'FAIL'} |"
            for g in s.gate
        ]
        lines.append("")
    per = s.metrics.get("per_category")
    if per:
        lines += ["| Category | Precision | Recall | F1 | Support |", "| --- | --- | --- | --- | --- |"]
        lines += [
            f"| {c} | {m['precision']} | {m['recall']} | {m['f1']} | {m['support']} |" for c, m in per.items()
        ]
        lines.append("")
    confusion = s.metrics.get("confusion")
    if confusion:
        cols = list(next(iter(confusion.values())).keys())
        lines += ["Confusion matrix (rows = expected, columns = predicted):", ""]
        lines += ["| expected \\ predicted | " + " | ".join(cols) + " |", "| --- |" + " --- |" * len(cols)]
        lines += [f"| {e} | " + " | ".join(str(row[c]) for c in cols) + " |" for e, row in confusion.items()]
        lines.append("")
    for key in ("per_session", "per_guard", "per_rubric", "override_rate", "runs_by_flow"):
        if s.metrics.get(key):
            lines += [f"{key}:", "", "```json", json.dumps(s.metrics[key], indent=1), "```", ""]
    failed = [c for c in s.cases if not c.passed]
    if failed:
        lines += [
            f"Failed cases ({len(failed)}):",
            "",
            "| Case | Expected | Actual | Error |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| {c.case_id} | {_cell(c.expected)} | {_cell(c.actual)} | {_cell(c.error or '')} |"
            for c in failed[:MAX_FAILED_CASES]
        ]
        if len(failed) > MAX_FAILED_CASES:
            lines.append(f"| ... {len(failed) - MAX_FAILED_CASES} more | | | |")
        lines.append("")
    lines += [f"- {n}" for n in s.notes]
    if s.notes:
        lines.append("")
    return lines


def to_markdown(run: EvalRun) -> str:
    lines = [
        f"# Eval run {run.run_id}",
        "",
        f"- Result: **{'PASSED' if run.ok else 'FAILED'}**",
        f"- Config version: `{run.config_version[:12]}`",
        f"- Mode: {run.mode}",
        f"- Model under test: {run.model or '-'}",
        f"- Judge: {run.judge or '-'}",
        f"- Started: {run.started_at.isoformat()}",
        "",
        "| Suite | Level | Status | Key metrics |",
        "| --- | --- | --- | --- |",
    ]
    for s in run.suites:
        key = ", ".join(
            f"{k}={_fmt(v) if isinstance(v, float) else v}" for k, v in _scalar_metrics(s.metrics)[:5]
        )
        lines.append(f"| {s.suite} | {int(s.level)} | {s.status.value} | {key} |")
    lines.append("")
    for s in run.suites:
        lines += _suite_md(s)
    return "\n".join(lines).rstrip() + "\n"


def write_reports(run: EvalRun, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"eval-{run.started_at.strftime('%Y%m%dT%H%M%S')}-{run.run_id[:8]}"
    js, md = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    js.write_text(to_json(run), encoding="utf-8")
    md.write_text(to_markdown(run), encoding="utf-8")
    return js, md
