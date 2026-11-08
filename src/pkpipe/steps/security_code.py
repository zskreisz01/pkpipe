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

from pkpipe.security.quarantine import EVIDENCE_CHARS, flag_domain, is_protected
from pkpipe.steps.base import StepContext, StepResult, register_code
from pkpipe.steps.learning_code import find_source_url

SECURITY_LOG_PORT = "security_log"


def flagged_url(ctx: StepContext, note: dict[str, Any] | None = None) -> str:
    item = ctx.item or {}
    candidates = [item.get("url"), (item.get("extract") or {}).get("url"), item.get("resource")]
    for value in candidates:
        if isinstance(value, str) and flag_domain(value):
            return value.strip()
    if isinstance(note, dict):
        found = find_source_url(note.get("front_matter"), note.get("body") or "")
        if found:
            return found
    return ""


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
        "flagged_at": now.isoformat(timespec="seconds"),
    }
    return StepResult(output=out, item={"security_domain": domain})


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
