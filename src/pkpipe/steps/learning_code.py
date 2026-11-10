"""Deterministic ``code`` function of the Process Learning Material flow (spec 012, BPMN: URL check).

``learning.prepare`` finds the source URL of an INBOX note and loads the prompt template. Only these two
values go on to the learning gateway (FR-002, constitution IV): the note's text is never handed to a model or
a notebook. The URL check here is the cheap first gate (scheme, literal private addresses); the gateway
repeats it with DNS resolution and the deny list before anything is fetched.
"""

from __future__ import annotations

import re
from typing import Any

from pkpipe.domain.urlfilter import UrlRejected, check_url
from pkpipe.steps.base import StepContext, StepError, StepResult, register_code

URL_KEYS = ("source", "url", "link")
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


def find_source_url(front_matter: dict[str, Any] | None, body: str) -> str | None:
    """Front matter ``source`` / ``url`` / ``link`` of the note, else the first http(s) URL of its body."""
    for key in URL_KEYS:
        value = (front_matter or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    match = _URL_IN_TEXT.search(body or "")
    return match.group(0).rstrip(".,;:") if match else None


@register_code("learning.prepare")
def prepare(ctx: StepContext, note: dict[str, Any], instruction: str) -> StepResult:
    """status ``ok`` (url + template), ``blocked`` (URL filter: item flagged for quarantine) or ``no_url``."""
    template = ctx.snapshot.asset(f"instructions/{instruction}")
    if template is None:
        raise StepError(f"instruction 'instructions/{instruction}' not found")
    url = find_source_url((note or {}).get("front_matter"), (note or {}).get("body", ""))
    if url is None:
        from pkpipe.steps.session_plan import image_has_description

        status = "image_note" if image_has_description(ctx.item) else "no_url"  # spec 032
        return StepResult(output={"status": status, "reason": "no_url", "url": "", "template": ""})
    from pkpipe.steps.secure_fetch import url_policy

    # the deny-list of the config plus the domains the security-flag handler deny-listed (spec 009)
    policy = url_policy(ctx)
    try:
        url = check_url(url, block_private_networks=policy.block_private_networks, deny=policy.deny)
    except UrlRejected as exc:
        reason = str(exc)
        ctx.ports.tracer.security_flag("url_filter", action="flag", reason_code=reason)
        flags = [{"detector": "url_filter", "reason_code": reason}]
        return StepResult(
            output={"status": "blocked", "reason": reason, "url": "", "template": ""},
            item={"flagged": True, "security_flags": flags},
        )
    return StepResult(output={"status": "ok", "reason": "", "url": url, "template": template.strip()})


_CLASSIFIED = re.compile(r"(?:^|[\s'\"(])(quota|auth): ")


def _failure_kind(message: str) -> str | None:
    """``quota`` or ``auth`` when the gateway message starts with that class (spec 032)."""
    found = _CLASSIFIED.search(message)
    return found.group(1) if found else None


_URL_BLOCKED = re.compile(r"\burl_blocked: ([a-z_]{1,40})\b")


@register_code("learning.create_failed")
def create_failed(ctx: StepContext, error: dict[str, Any] | None) -> StepResult:
    """Error branch of ``learning:create`` (SC-003).

    The gateway refuses a URL with ``url_blocked: <reason code>`` after DNS resolution and the deny
    list, which the cheap first check in :func:`prepare` cannot do. Such an item is flagged for
    quarantine (it would be refused on every retry). Any other error (service down, invalid
    configuration) fails the step again, so the item stays ``released`` and the next run retries it.
    """
    message = str((error or {}).get("message") or "")
    match = _URL_BLOCKED.search(message)
    if match is None:
        kind = _failure_kind(message)
        if kind:
            # spec 032: a quota wait or an expired login is not the item's failure; the item stays released
            # and the next run tries again. The event says which one it is and until when.
            ctx.event("learning_wait", kind, reason=message[:300])
        raise StepError(f"learning service: {message[:300] or 'create failed'}")
    reason = match.group(1)
    ctx.ports.tracer.security_flag("url_filter", action="flag", reason_code=reason)
    return StepResult(
        output={"status": "blocked", "reason": reason},
        item={"flagged": True, "security_flags": [{"detector": "url_filter", "reason_code": reason}]},
    )
