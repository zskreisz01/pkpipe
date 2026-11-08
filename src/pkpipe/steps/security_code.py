"""Deterministic ``code`` functions of the ``handle_security_flag`` flow (spec 009 US2, BPMN: Handle
Security Flag): log the malicious URL, deny-list its domain. Quarantine is the vault's ``quarantine_note``
tool, the alert a ``notify`` step; the flow sets the item state.

Which URL: the item's ``url`` (set by the flows from the note's front matter or the run input), the
secure-fetch extract's URL, an http(s) ``resource``, else the source URL of the note the flow read. An item
without a URL is still logged (``url: ''``, no domain), quarantined and alerted; there is nothing to deny.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pkpipe.domain.item_events import ctx_event
from pkpipe.security.quarantine import EVIDENCE_CHARS, flag_domain, is_protected
from pkpipe.steps.base import StepContext, StepResult, register_code
from pkpipe.steps.learning_code import find_source_url

SECURITY_LOG_PORT = "security_log"


def flagged_url(ctx: StepContext, note: dict[str, Any] | None = None) -> str:
    item = ctx.item or {}
    candidates = [
        item.get("url"),
        item.get("source_url"),
        (item.get("extract") or {}).get("url"),
        item.get("resource"),
    ]
    for value in candidates:
        if isinstance(value, str) and flag_domain(value):
            return value.strip()
    if isinstance(note, dict):
        found = find_source_url(note.get("front_matter"), note.get("body") or "")
        if found:
            return found
    return ""


def evidence_text(flags: list[dict[str, Any]]) -> str:
    """One line for the alert: the scores of the detectors and, for the classifier, of each model
    (numbers and labels only, spec 029)."""
    parts: list[str] = []
    for f in flags:
        name = str(f.get("flag") or f.get("detector") or "flag")
        score = f.get("score")
        detail = f.get("detail") if isinstance(f.get("detail"), dict) else None
        text = name if not isinstance(score, int | float) else f"{name} {float(score):.2f}"
        if detail and detail.get("members"):
            models = ", ".join(
                f"{m['model']} {float(m['max_score']):.2f}" + (f" {m['label']}" if m.get("label") else "")
                for m in detail["members"]
            )
            text += f" ({models})"
        parts.append(text)
    return "; ".join(parts)


@register_code("security.record_flag")
def record_flag(ctx: StepContext, read: str = "read") -> StepResult:
    """One ``malicious_urls.jsonl`` row per flag of the item and the URL's domain on the deny-list (once;
    flagged again, its counter goes up). ``read`` names the node that read the item's note (may be absent)."""
    log = ctx.ports.require(SECURITY_LOG_PORT)
    item = ctx.item or {}
    note = ctx.resolve(read, required=False)
    url = flagged_url(ctx, note if isinstance(note, dict) else None)
    domain = flag_domain(url)
    now = datetime.fromtimestamp(ctx.ports.clock.now(), UTC)
    flags = [f for f in item.get("security_flags") or [] if isinstance(f, dict)] or [
        {"flag": "unspecified", "detector": "unknown", "reason_code": "no_flag_details"}
    ]
    for f in flags:
        log.log_malicious_url(
            {
                "url": url,
                "domain": domain,
                "flag": str(f.get("flag") or f.get("detector") or "unspecified"),
                "detector": str(f.get("detector") or "unknown"),
                "score": f.get("score") if isinstance(f.get("score"), int | float) else None,
                "evidence": str(f.get("reason_code") or "")[:EVIDENCE_CHARS] or None,
                "detail": f.get("detail") if isinstance(f.get("detail"), dict) else None,
                "item_id": item.get("id"),
                "run_id": ctx.run.run_id,
                "timestamp": now,
            }
        )
    url_filter = (ctx.snapshot.data.get("security") or {}).get("url_filter") or {}
    protected = url_filter.get("protected_domains") or []
    deny: dict[str, Any] | None
    if domain and is_protected(domain, protected):
        deny = {"domain": domain, "added": False, "count": 0, "protected": True}  # spec 028: host stays open
    else:
        deny = log.deny_domain(domain, day=now.date().isoformat()) if domain else None
    out = {
        "url": url,
        "domain": domain,
        "logged": len(flags),
        "deny": deny,
        "flags": sorted({str(f.get("flag") or f.get("detector")) for f in flags}),
        "flag_text": ", ".join(sorted({str(f.get("flag") or f.get("detector")) for f in flags})),
        "evidence_text": evidence_text(flags),
        "flagged_at": now.isoformat(timespec="seconds"),
    }
    ctx_event(
        ctx,
        "quarantined",
        "flagged",
        url=url,
        domain=domain,
        codes={
            "reasons": out["flag_text"],
            "deny_listed": bool(deny and deny.get("added")),
            "protected": bool(deny and deny.get("protected")),
        },
    )
    return StepResult(output=out, item={"security_domain": domain})


HELD_REASON = "held for your decision: only the injection detector fired (classifier warning)"


@register_code("security.hold_for_review")
def hold_for_review(ctx: StepContext) -> StepResult:
    """BPMN: Hold for the owner (spec 029). The secure-fetch verdict found only a classifier warning of the
    injection detector (the safety model said Safe, heuristics and Reader found nothing). The item is not
    quarantined and its domain is not deny-listed: it is listed as failed with a fixed reason, and the
    evidence (scores and labels, no text) is kept in the app DB for the triage page. The owner accepts the
    warning (the item goes through the release run again) or leaves it. The item ends here."""
    item = ctx.item or {}
    review = item.get("classifier_review") if isinstance(item.get("classifier_review"), dict) else {}
    port = ctx.ports.extra.get("classifier_reviews")
    if port is not None and item.get("id"):
        port.hold(str(item["id"]), flagged_url(ctx, None), review.get("evidence") or {})
    ctx_event(
        ctx, "held", "held", url=flagged_url(ctx, None), codes={"classifier": review.get("evidence") or {}}
    )
    fields = {"processing_status": "failed", "failure_reason": HELD_REASON}
    return StepResult(output={**fields, "held": True}, item=fields)


@register_code("security.quarantined")
def quarantined(
    ctx: StepContext, note: str = "quarantine", url_only: str = "quarantine_url"
) -> dict[str, Any]:
    """The quarantine result of whichever branch ran (a note moved, or a metadata note for a URL-only item);
    ``note`` / ``url_only`` name the nodes."""
    for ref in (note, url_only):
        result = ctx.resolve(ref, required=False)
        if isinstance(result, dict) and result.get("path"):
            return {"path": result["path"], "moved": bool(result.get("moved"))}
    return {"path": "", "moved": False}
