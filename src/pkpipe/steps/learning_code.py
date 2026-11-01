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
        return StepResult(output={"status": "no_url", "reason": "no_url", "url": "", "template": ""})
    block = bool(
        ((ctx.snapshot.data.get("security") or {}).get("url_filter") or {}).get(
            "block_private_networks", True
        )
    )
    try:
        url = check_url(url, block_private_networks=block)
    except UrlRejected as exc:
        reason = str(exc)
        ctx.ports.tracer.security_flag("url_filter", action="flag", reason_code=reason)
        flags = [{"detector": "url_filter", "reason_code": reason}]
        return StepResult(
            output={"status": "blocked", "reason": reason, "url": "", "template": ""},
            item={"flagged": True, "security_flags": flags},
        )
    return StepResult(output={"status": "ok", "reason": "", "url": url, "template": template.strip()})


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
        raise StepError(f"learning service: {message[:300] or 'create failed'}")
    reason = match.group(1)
    ctx.ports.tracer.security_flag("url_filter", action="flag", reason_code=reason)
    return StepResult(
        output={"status": "blocked", "reason": reason},
        item={"flagged": True, "security_flags": [{"detector": "url_filter", "reason_code": reason}]},
    )
