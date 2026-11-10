"""The pipeline overview (spec 033): where every item stands, what needs the owner, what is waiting, what is
done, in one structure. Pure logic: tables and counters in, a dict and a Markdown note out.

The UI page ``/pipeline``, the API, the app MCP tool, the CLI and the vault note ``Pipeline.md`` all read the
same dict, so they cannot disagree. Every item of the triage table lands in exactly one bucket (``bucket``),
so the funnel adds up to the table. Only ids, codes, URLs and the short sentences kept in the unfinished-items
table (spec 031, already schema-checked and escaped) appear; no page, note or image text.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pkpipe.domain import in_progress as ip

#: ``(code, stage, label)``: the one bucket of every triage row, in funnel order.
BUCKETS: tuple[tuple[str, str, str], ...] = (
    ("waiting_confirmation", "triaged", "waiting for your confirmation"),
    ("awaiting_category", "triaged", "awaiting a category"),
    ("released", "released", "released: next run"),
    ("waiting_quota", "released", "waiting for NotebookLM quota"),
    ("done", "processed", "done"),
    ("ready", "processed", "ready (notebook)"),
    ("held", "processed", "held for you"),
    ("failed", "processed", "failed"),
    ("unprocessed", "processed", "not finished"),
    ("result_unknown", "processed", "processed (result unknown)"),
    ("quarantined", "stopped", "quarantined"),
)
STAGES: tuple[tuple[str, str], ...] = (
    ("triaged", "Triaged"),
    ("released", "Released"),
    ("processed", "Processed"),
    ("stopped", "Stopped"),
)
BUCKET_LABEL = {code: label for code, _, label in BUCKETS}
BUCKET_STAGE = {code: stage for code, stage, _ in BUCKETS}
#: buckets that need the owner, in the order they are shown
NEEDS_ORDER = ("held", "quarantined", "failed", "unprocessed", "awaiting_category", "waiting_confirmation")
NEXT_STEPS = {
    **ip.NEXT_STEPS,
    "needs_category": "Open the triage page and pick a category (the model found none).",
    "awaiting_category": "Decide on the proposed new category or pick an existing one.",
    "new_category_proposal": "Read the proposal note, then confirm or override the category.",
    "waiting_confirmation": "Confirm or override the suggested category in the triage page.",
}
ACTIONS = {
    "held": ("accept", "Accept warning and process"),
    "quarantined": ("release", "Release from quarantine"),
    "failed": ("retry", "Retry"),
    "unprocessed": ("retry", "Retry"),
}
DEFAULT_RECENT_DAYS = 14
HEADING = "# Pipeline overview"
GENERATED = "Generated:"
MAX_NOTE_ROWS = 40
MAX_RESOURCE = 100


def bucket(
    row: Mapping[str, Any], *, status: str = "", held: bool = False, waiting_quota: bool = False
) -> str:
    """The one bucket of a triage row (``status`` is its processing-list status, ``held`` an open classifier
    warning, ``waiting_quota`` a notebook that waits for a NotebookLM quota window)."""
    state = str(row.get("state") or "")
    if state in ("suggested", "pending"):
        return "waiting_confirmation"
    if state == "awaiting_category":
        return "awaiting_category"
    if state == "quarantined":
        return "quarantined"
    if state == "released":
        return "waiting_quota" if waiting_quota else "released"
    if state == "processed":
        code, _ = ip.outcome(state, status, held=held, failed=status == "failed")
        return {"unprocessed": "unprocessed", "processed": "result_unknown"}.get(code, code)
    return "result_unknown"


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _stamp(value: Any) -> str:
    """``2026-11-10 10:31`` (UTC), or empty."""
    parsed = _parse_time(value)
    return parsed.astimezone(UTC).strftime("%Y-%m-%d %H:%M") if parsed else ""


def _short(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def reason_of(
    code: str, row: Mapping[str, Any], progress: Mapping[str, Any] | None, status: str, link: str
) -> str:
    """A stable code for why the item needs the owner."""
    if code == "held":
        return "held_classifier"
    if code == "quarantined":
        return "quarantined"
    if code == "awaiting_category":
        return "new_category_proposal" if row.get("override") else "awaiting_category"
    if code == "waiting_confirmation":
        return "needs_category" if row.get("needs_owner") else "waiting_confirmation"
    if progress and progress.get("cause"):
        return str(progress["cause"])
    text = "" if link.startswith(("http://", "https://")) else link
    return ip.cause_code("processed", status, text, held=False)


def _item_record(
    row: Mapping[str, Any],
    *,
    processing: Mapping[str, Any],
    progress: Mapping[str, Any] | None,
    job: Mapping[str, Any] | None,
    held: bool,
    retries: int,
    retry_max: int,
    auto_passed: bool,
    finished_at: str,
) -> dict[str, Any]:
    status = str(processing.get("status") or "")
    link = str(processing.get("link") or "")
    code = bucket(
        row, status=status, held=held, waiting_quota=bool(job and job.get("status") == "waiting_quota")
    )
    reason = reason_of(code, row, progress, status, link)
    at_limit = code in ("held", "failed", "unprocessed") and retries >= retry_max
    if at_limit and code != "held":
        reason = "retry_limit"
    step = (progress or {}).get("next_step") if not at_limit else None
    if at_limit and code == "held":
        step = NEXT_STEPS["retry_limit"]
    note = str(processing.get("note") or "")
    return {
        "item_id": row["item_id"],
        "bucket": code,
        "stage": BUCKET_STAGE[code],
        "label": BUCKET_LABEL[code],
        "kind": str(row.get("kind") or ""),
        "category": str(row.get("override") or row.get("ai_suggested_category") or ""),
        "resource": _short(row.get("resource"), MAX_RESOURCE),
        "reason": reason,
        "situation": _short((progress or {}).get("situation"), ip.MAX_SITUATION),
        "next_step": _short(step or NEXT_STEPS.get(reason, NEXT_STEPS["unfinished"]), ip.MAX_NEXT),
        "retries": retries,
        "retry_limit": retry_max,
        "at_retry_limit": at_limit,
        "auto_passed": auto_passed,
        "note": note if note.endswith(".md") else "",
        "link": link if link.startswith(("http://", "https://")) else "",
        "notebook": str(processing.get("nb_id") or ""),
        "podcast": ("ready" if job.get("audio") else "pending") if job and code in ("ready", "done") else "",
        "waiting_until": _stamp_epoch(job.get("retry_after")) if job and code == "waiting_quota" else "",
        "finished_at": finished_at,
    }


def _stamp_epoch(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value), UTC).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def build_overview(
    *,
    rows: Sequence[Mapping[str, Any]],
    processing: Mapping[str, Mapping[str, Any]] | None = None,
    progress: Mapping[str, Mapping[str, Any]] | None = None,
    held: Sequence[str] = (),
    retries: Mapping[str, int] | None = None,
    retry_max: int = 3,
    auto_passed: Sequence[str] = (),
    last_seen: Mapping[str, str] | None = None,
    runs: Sequence[Mapping[str, Any]] = (),
    run_counts: Mapping[str, Mapping[str, int]] | None = None,
    next_runs: Sequence[Mapping[str, Any]] = (),
    jobs: Mapping[str, Mapping[str, Any]] | None = None,
    captured: int | None = None,
    where: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    recent_days: int = DEFAULT_RECENT_DAYS,
) -> dict[str, Any]:
    """The overview dict (docs: specs/033-pipeline-overview/data-model.md)."""
    now = now or datetime.now(UTC)
    processing, progress, jobs = processing or {}, progress or {}, jobs or {}
    retries, last_seen = retries or {}, last_seen or {}
    held_set, auto_set = set(held), set(auto_passed)
    items = [
        _item_record(
            r,
            processing=processing.get(r["item_id"], {}),
            progress=progress.get(r["item_id"]),
            job=jobs.get(r["item_id"]),
            held=r["item_id"] in held_set,
            retries=int(retries.get(r["item_id"], 0)),
            retry_max=retry_max,
            auto_passed=r["item_id"] in auto_set,
            finished_at=_stamp(last_seen.get(r["item_id"])),
        )
        for r in rows
    ]
    counts = {code: 0 for code, _, _ in BUCKETS}
    for it in items:
        counts[it["bucket"]] += 1
    funnel = {
        "captured": captured,
        "total": len(items),
        "buckets": counts,
        "stages": [
            {
                "stage": stage,
                "label": label,
                "count": sum(c for code, c in counts.items() if BUCKET_STAGE[code] == stage),
                "buckets": [
                    {"code": code, "label": lab, "count": counts[code]}
                    for code, st, lab in BUCKETS
                    if st == stage
                ],
            }
            for stage, label in STAGES
        ],
    }
    needs = sorted(
        (it for it in items if it["bucket"] in NEEDS_ORDER and not _quiet(it)),
        key=lambda it: (NEEDS_ORDER.index(it["bucket"]), it["at_retry_limit"], it["item_id"]),
    )
    for it in needs:
        action = ACTIONS.get(it["bucket"])
        if it["at_retry_limit"]:
            action = None
        it["action"] = {"kind": action[0], "label": action[1]} if action else None
    waiting = {
        "released": [it for it in items if it["bucket"] == "released"],
        "waiting_quota": [it for it in items if it["bucket"] == "waiting_quota"],
        "generating": [
            {"item_id": i, "since": _stamp_epoch(j.get("started_at"))}
            for i, j in sorted(jobs.items())
            if j.get("status") == "generating"
        ],
        "next_runs": [dict(n) for n in next_runs],
    }
    cutoff = now - timedelta(days=recent_days)
    done = [
        it
        for it in items
        if it["bucket"] in ("done", "ready")
        and (t := _parse_time(it["finished_at"])) is not None
        and t >= cutoff
    ]
    done.sort(key=lambda it: (it["finished_at"], it["item_id"]), reverse=True)
    return {
        "generated_at": now.astimezone(UTC).strftime("%Y-%m-%d %H:%M"),
        "funnel": funnel,
        "runs": _last_runs(runs, run_counts or {}),
        "needs_you": needs,
        "waiting": waiting,
        "done_recent": done,
        "recent_days": recent_days,
        "where": dict(where or {}),
        "items": {it["item_id"]: it for it in items},
    }


def _quiet(it: Mapping[str, Any]) -> bool:
    """Items that are on the list only because of a state but have nothing for the owner to do yet."""
    return it["bucket"] == "waiting_confirmation" and it["reason"] != "needs_category"


def _last_runs(
    runs: Sequence[Mapping[str, Any]], counts: Mapping[str, Mapping[str, int]]
) -> list[dict[str, Any]]:
    latest: dict[str, Mapping[str, Any]] = {}
    for r in runs:
        flow = str(r.get("flow_id"))
        if flow not in latest or str(r.get("started_at")) > str(latest[flow].get("started_at")):
            latest[flow] = r
    out = []
    for flow in sorted(latest):
        r = latest[flow]
        c = counts.get(str(r.get("run_id")), {})
        out.append(
            {
                "flow": flow,
                "run_id": str(r.get("run_id")),
                "status": str(r.get("status")),
                "trigger": str(r.get("trigger")),
                "started": _stamp(r.get("started_at")),
                "ended": _stamp(r.get("ended_at")),
                "items": int(c.get("items", 0)),
                "failed_steps": int(c.get("failed_steps", 0)),
            }
        )
    return out


def position(overview: Mapping[str, Any], item_id: str) -> dict[str, Any] | None:
    """Where one item is now (the header line of its history page): bucket, stage, label, next step."""
    it = (overview.get("items") or {}).get(item_id)
    if it is None:
        return None
    keys = ("bucket", "stage", "label", "reason", "situation", "next_step", "waiting_until", "auto_passed")
    return {k: it[k] for k in keys}


# -- Markdown -------------------------------------------------------------------------------------------
def md(value: Any, limit: int = 120) -> str:
    """One table cell: single line, no pipes, no markup (the same rule as the unfinished-items table)."""
    return ip.cell(value, limit)


def _code(value: Any, limit: int = MAX_RESOURCE) -> str:
    text = _short(value, limit).replace("`", "'").replace("|", "/")
    return f"`{text}`" if text else ""


def _link(label: str, ui_url: str | None, path: str) -> str:
    return f"[{label}]({ui_url.rstrip('/')}{path})" if ui_url else ""


def _more(total: int, shown: int, ui_url: str | None) -> list[str]:
    if total <= shown:
        return []
    see = f" See {_link('the Pipeline page', ui_url, '/pipeline')}." if ui_url else ""
    return [f"\n+{total - shown} more.{see}"]


def render_markdown(ov: Mapping[str, Any], *, ui_url: str | None = None) -> str:
    """``Pipeline.md``: plain Markdown tables, no Dataview, no secrets. The line starting with ``Generated:``
    is ignored when comparing (see :func:`normalize`), so a refresh that changes nothing writes nothing."""
    f = ov["funnel"]
    out = [
        HEADING,
        "",
        "Machine-owned: pkpipe rewrites this note after every triage and release run. Do not edit it "
        "by hand. Decide in the web UI; every row links to the item's history.",
        "",
        f"{GENERATED} {ov['generated_at']} UTC" + (f" · UI: {ui_url.rstrip('/')}/pipeline" if ui_url else ""),
        "",
        "## Funnel",
        "",
    ]
    captured = f.get("captured")
    cap = f"captured, not yet triaged: **{captured}** → " if captured is not None else ""
    parts = " → ".join(f"{s['label'].lower()} **{s['count']}**" for s in f["stages"])
    out += [f"{cap}{parts} (items in the triage table: {f['total']})", ""]
    out += ["| stage | result | items |", "| --- | --- | --- |"]
    for stage in f["stages"]:
        for b in stage["buckets"]:
            if b["count"]:
                out.append(f"| {md(stage['label'], 30)} | {md(b['label'], 40)} | {b['count']} |")
    out += ["", "## Last runs", ""]
    if ov["runs"]:
        out += [
            "| flow | status | started (UTC) | items touched | failed steps |",
            "| --- | --- | --- | --- | --- |",
        ]
        for r in ov["runs"]:
            out.append(
                f"| {md(r['flow'], 40)} | {md(r['status'], 20)} | {r['started']} | {r['items']} "
                f"| {r['failed_steps']} |"
            )
    else:
        out.append("No run yet.")
    needs = ov["needs_you"]
    out += ["", f"## Needs you ({len(needs)})", ""]
    if needs:
        out += ["| item | what | situation | next step | open |", "| --- | --- | --- | --- | --- |"]
        for it in needs[:MAX_NOTE_ROWS]:
            opened = " · ".join(
                x
                for x in (
                    _link("history", ui_url, f"/items/{it['item_id']}"),
                    _link(
                        (it["action"] or {}).get("label", "act") if it.get("action") else "decide",
                        ui_url,
                        f"/pipeline#item-{it['item_id']}",
                    ),
                )
                if x
            )
            what = f"{it['label']} · {it['reason']}" + (f" · {it['resource']}" if it["resource"] else "")
            out.append(
                f"| {it['item_id']} | {md(what, 140)} | {md(it['situation'], ip.MAX_SITUATION)} "
                f"| {md(it['next_step'], ip.MAX_NEXT)} | {opened} |"
            )
        out += _more(len(needs), MAX_NOTE_ROWS, ui_url)
    else:
        out.append("Nothing waits for you.")
    w = ov["waiting"]
    out += ["", "## In progress / waiting", ""]
    out.append(f"- Released for the next release run: **{len(w['released'])}**")
    out.append(f"- Waiting for NotebookLM quota: **{len(w['waiting_quota'])}**")
    for it in w["waiting_quota"][:MAX_NOTE_ROWS]:
        until = f" (until {it['waiting_until']} UTC)" if it["waiting_until"] else ""
        out.append(f"  - {it['item_id']}{until}")
    out.append(f"- Notebooks or podcasts being generated: **{len(w['generating'])}**")
    for g in w["generating"][:MAX_NOTE_ROWS]:
        out.append(f"  - {g['item_id']}" + (f" (since {g['since']} UTC)" if g["since"] else ""))
    if w["next_runs"]:
        out += ["", "| next run | flow | schedule |", "| --- | --- | --- |"]
        for n in w["next_runs"]:
            out.append(f"| {md(n.get('at'), 30)} | {md(n.get('flow'), 40)} | `{n.get('cron', '')}` |")
    done = ov["done_recent"]
    out += ["", f"## Done in the last {ov['recent_days']} days ({len(done)})", ""]
    if done:
        out += ["| item | category | result | finished (UTC) |", "| --- | --- | --- | --- |"]
        for it in done[:MAX_NOTE_ROWS]:
            out.append(
                f"| {_link(it['item_id'], ui_url, '/items/' + it['item_id']) or it['item_id']} "
                f"| {md(it['category'], 30)} | {_result(it)} | {it['finished_at']} |"
            )
        out += _more(len(done), MAX_NOTE_ROWS, ui_url)
    else:
        out.append("Nothing was closed in this time (items closed before the event log are not listed).")
    out += ["", "## Where things live", ""]
    out += [f"- {md(k, 40)}: {v}" for k, v in _where_lines(ov.get("where") or {}, ui_url)]
    return "\n".join(out).rstrip() + "\n"


def _result(it: Mapping[str, Any]) -> str:
    bits = []
    if it["note"]:
        bits.append(f"[[{it['note'][:-3]}]]")
    if it["link"]:
        bits.append(f"[notebook]({it['link']})")
    if it["podcast"]:
        bits.append(f"podcast {it['podcast']}")
    return " · ".join(bits) or "—"


def _where_lines(where: Mapping[str, Any], ui_url: str | None) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    if ui_url:
        lines.append(("Decide and act (web UI)", f"{ui_url.rstrip('/')}/pipeline"))
    for key, label in (
        ("inbox_list", "Capture list"),
        ("in_progress", "Unfinished items"),
        ("processing_list", "Processing list"),
        ("quarantine", "Quarantine folder"),
        ("pipeline_file", "This note"),
    ):
        if where.get(key):
            lines.append((label, f"`{where[key]}`"))
    for res in where.get("results") or []:
        lines.append((f"Results: {res['folder']}", f"`{res['index']}`"))
    return lines


def normalize(text: str | None) -> str:
    """The note without its ``Generated:`` line: what two refreshes are compared by."""
    return "\n".join(line for line in (text or "").splitlines() if not line.startswith(GENERATED))


def content_hash(text: str | None) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:16]


# -- the one assembler (UI, API, MCP, CLI and the flow step all call it) -----------------------------------
def where_map(layout: Any, results: Sequence[Mapping[str, str]] = ()) -> dict[str, Any]:
    """The fixed 'where things live' map from the vault layout (paths only)."""
    return {
        "inbox_list": layout.inbox_list_file or f"{layout.inbox_dir}/ (one note per item)",
        "in_progress": layout.in_progress,
        "processing_list": layout.processing_list,
        "quarantine": layout.quarantine_dir,
        "pipeline_file": layout.pipeline,
        "results": [dict(r) for r in results],
    }


def assemble(
    *,
    triage_rows: Sequence[Mapping[str, Any]],
    processing_items: Sequence[Mapping[str, Any]] = (),
    progress_rows: Sequence[Mapping[str, Any]] = (),
    stats: Any = None,
    run_history: Any = None,
    learning_jobs: Any = None,
    next_runs: Sequence[Mapping[str, Any]] = (),
    inbox_ids: Sequence[str] | None = None,
    retry_max: int = 3,
    where: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    recent_days: int = DEFAULT_RECENT_DAYS,
    run_limit: int = 200,
) -> dict[str, Any]:
    """Read the helper tables (``stats``: a PipelineStatsPort; ``run_history``: a RunStorePort;
    ``learning_jobs``: a callable) and build the overview. A helper that fails leaves its part empty: the
    overview is for orientation and must never be the reason a page or a run fails."""

    def safe(fn: Any, default: Any) -> Any:
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return default

    now = now or datetime.now(UTC)
    held = safe(lambda: [i for i, r in stats.held().items() if not r.get("waived")], []) if stats else []
    retries = safe(stats.retry_counts, {}) if stats else {}
    auto = safe(lambda: stats.item_ids_with_event("auto_passed"), set()) if stats else set()
    last_seen = safe(stats.last_event_times, {}) if stats else {}
    runs = safe(lambda: run_history.list_runs()[-run_limit:], []) if run_history else []
    counts: dict[str, dict[str, int]] = {}
    if stats and runs:
        latest = {str(r.get("flow_id")): r for r in runs}  # newest per flow is enough (list is oldest first)
        ids = [str(r["run_id"]) for r in latest.values() if r.get("run_id")]
        counts = safe(lambda: stats.run_item_counts(ids), {})
    jobs = safe(learning_jobs, {}) if learning_jobs else {}
    triage_ids = {str(r["item_id"]) for r in triage_rows}
    captured = None if inbox_ids is None else len([i for i in inbox_ids if i not in triage_ids])
    return build_overview(
        rows=triage_rows,
        processing={str(i["item_id"]): i for i in processing_items if i.get("item_id")},
        progress={str(r["item_id"]): r for r in progress_rows if r.get("item_id")},
        held=held,
        retries=retries,
        retry_max=retry_max,
        auto_passed=sorted(auto),
        last_seen=last_seen,
        runs=runs,
        run_counts=counts,
        next_runs=next_runs,
        jobs=jobs,
        captured=captured,
        where=where,
        now=now,
        recent_days=recent_days,
    )
