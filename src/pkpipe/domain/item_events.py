"""The event log of one item (spec 030). Pure logic: safe recording and the timeline the owner reads.

Every step of a flow run that touches an item appends one ``step`` event; steps with something worth
explaining (the secure-fetch verdict, a quarantine, a hold, a source search) add their own events. Owner
actions are already audited (spec 014) and are merged into the timeline when it is read, so nothing is
written twice. Events carry ids, numbers and codes. They never carry page, note, journal, context or image
text; a flow of a private data class records content-free events (kind, step, outcome, duration only).
"""

from __future__ import annotations

import logging
from typing import Any

from pkpipe.domain.data_classes import is_private

log = logging.getLogger("pkpipe.item_events")

KIND_STEP = "step"
KIND_VERDICT = "verdict"
KIND_SOURCE = "source"
KIND_QUARANTINED = "quarantined"
KIND_HELD = "held"
KIND_OWNER = "owner"
KIND_AUTO_PASSED = "auto_passed"
KIND_LEARNING_WAIT = "learning_wait"
KIND_RESULT = "result"
KIND_IN_PROGRESS = "in_progress"
MAX_TEXT = 300


def safe_detail(detail: dict[str, Any] | None, *, private: bool) -> dict[str, Any]:
    """Short scalars, lists and dicts only; nothing at all for a private data class."""
    if private or not detail:
        return {}
    out: dict[str, Any] = {}
    for key, value in detail.items():
        if value is None:
            continue
        if isinstance(value, str):
            out[key] = value[:MAX_TEXT]
        elif isinstance(value, bool | int | float | list | dict):
            out[key] = value
        else:
            out[key] = str(value)[:MAX_TEXT]
    return out


def emit(
    port: Any,
    item_id: str | None,
    kind: str,
    *,
    run_id: str | None = None,
    flow_id: str | None = None,
    step: str | None = None,
    outcome: str | None = None,
    detail: dict[str, Any] | None = None,
    codes: dict[str, Any] | None = None,
    data_classes: object = None,
) -> None:
    """Append one event. ``detail`` is dropped in a private flow; ``codes`` (fixed reason codes, flags and
    counters, never text of an item) always stay. A broken log must never stop a run: failures are logged."""
    if port is None or not item_id:
        return
    try:
        port.record(
            str(item_id),
            kind,
            run_id=run_id,
            flow_id=flow_id,
            step=step,
            outcome=outcome,
            detail={
                **safe_detail(detail, private=is_private(data_classes)),
                **safe_detail(codes, private=False),
            },
        )
    except Exception:  # noqa: BLE001
        log.warning("item event %s of %s was not recorded", kind, item_id, exc_info=True)


def describe(event: dict[str, Any]) -> str:
    """One fixed sentence for the timeline row (no page text)."""
    kind, outcome, d = event.get("kind"), event.get("outcome"), event.get("detail") or {}
    step = event.get("step") or ""
    if kind == KIND_STEP:
        base = f"{step or 'step'} ({d.get('type', '?')}): {outcome or 'done'}"
        if d.get("ms") is not None:
            base += f" in {d['ms']} ms"
        if d.get("error"):
            base += f" - {d['error']}"
        return base
    if kind == KIND_VERDICT:
        flags = ", ".join(d.get("flags") or []) or "none"
        return f"secure fetch verdict: {outcome}; flags: {flags}"
    if kind == KIND_SOURCE:
        return f"source search: {outcome}" + (f" ({d['reason']})" if d.get("reason") else "")
    if kind == KIND_QUARANTINED:
        return f"quarantined ({d.get('reasons') or 'security flag'})"
    if kind == KIND_HELD:
        return "held for the owner: classifier warning of the injection detector only"
    if kind == KIND_IN_PROGRESS:
        labels = {
            "described": "situation written in INBOX-InProgress",
            "carried": "INBOX-InProgress row kept (nothing changed)",
            "fallback": "INBOX-InProgress row written with the fixed sentence",
            "closed": "closed: left INBOX-InProgress, entry archived",
            "reopened": "open again: left INBOX-InProgress, entry back in the INBOX list",
        }
        return labels.get(str(outcome), f"INBOX-InProgress: {outcome}")
    if kind == KIND_AUTO_PASSED:
        return (
            "auto-passed: only the injection detector fired (safety model Safe, heuristics and Reader "
            "clean); the owner's auto_pass rule let the page through"
        )
    if kind == KIND_LEARNING_WAIT:
        return f"learning service: waiting ({outcome}); the next run tries again"
    if kind == KIND_RESULT:
        return f"result written: {d.get('path') or d.get('what') or 'note'}"
    if kind == KIND_OWNER:
        return f"owner: {d.get('action')}"
    return f"{kind}: {outcome or ''}".strip()


def timeline(events: list[dict[str, Any]], audit: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Events and the owner's audited actions on the item, oldest first, each with its sentence."""
    rows = [dict(e) for e in events]
    for a in audit:
        rows.append(
            {
                "at": a["at"],
                "kind": KIND_OWNER,
                "step": None,
                "run_id": (a.get("detail") or {}).get("run_id"),
                "flow_id": None,
                "outcome": "refused" if str(a["action"]).endswith("_refused") else "done",
                "detail": {"action": a["action"], "actor": a["actor"], **(a.get("detail") or {})},
            }
        )
    rows.sort(key=lambda r: str(r["at"]))
    for r in rows:
        r["text"] = describe(r)
    return rows


def ctx_event(
    ctx: Any, kind: str, outcome: str | None = None, *, codes: dict[str, Any] | None = None, **detail: Any
) -> None:
    """``ctx.event`` for code that may run with a bare context (a unit test)."""
    event = getattr(ctx, "event", None)
    if callable(event):
        event(kind, outcome, codes=codes, **detail)
