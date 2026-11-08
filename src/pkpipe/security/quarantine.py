"""Security-flag handling (spec 009 US2, data-model): the malicious URL entry, the deny-list file edit and
the inert quarantine note format. Pure logic; the files are written by adapters (``SecurityLogPort``, the
vault's ``quarantine_note`` tool).

Inert form: the quarantined note keeps the original text verbatim inside a fenced code block whose fence is
longer than any backtick run in the text, so neither Obsidian nor an agent reading the vault renders it as
markdown, links, embeds or front matter; ``security_flag`` front matter names flags and reason codes only.
Releasing restores the text exactly (:func:`unwrap_inert`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

SecurityFlagName = Literal[
    "malicious_content",
    "injection_or_jailbreak",
    "canary_leak",
    "url_filter",
    "heuristic",
    "classifier",
    "canary",
    "reader",
    "viewer",
]
EVIDENCE_CHARS = 200
_PKPIPE_COMMENT = re.compile(r"\s*#\s*pkpipe:.*$")
_COUNT = re.compile(r"#\s*pkpipe:\s*flagged\s+(\d+)")


class MaliciousUrlEntry(BaseModel):
    """One row of ``security/malicious_urls.jsonl`` per flag (data-model). ``evidence`` holds reason codes,
    never page or note text (truncated to 200 characters)."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(description="Source URL of the flagged item ('' when it has none)")
    domain: str | None = Field(default=None, description="Host that is deny-listed (None without a URL)")
    flag: str = Field(description="Flag name (output guard or secure-fetch verdict)")
    detector: str
    score: float | None = None
    evidence: str | None = Field(default=None, max_length=EVIDENCE_CHARS)
    detail: dict[str, Any] | None = Field(
        default=None, description="Spec 029: what each classifier model said (scores, labels; no text)"
    )
    item_id: str | None = None
    run_id: str
    timestamp: datetime


def flag_domain(url: str | None) -> str | None:
    """Host to deny-list for a flagged URL: lower case, without ``www.``; None for anything but http(s)."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").strip(".").lower()
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    return host.removeprefix("www.") or None


def is_protected(domain: str, protected: Iterable[str]) -> bool:
    """Whether ``domain`` is a protected host or a subdomain of one (spec 028)."""
    d = domain.lower().strip(".")
    return any(d == p or d.endswith("." + p) for p in (str(x).lower().strip(". ") for x in protected) if p)


def _line_domain(line: str) -> str:
    text = line.split("#", 1)[0].strip().lower()
    if "://" in text:
        text = urlsplit(text).hostname or ""
    return text.removeprefix("*.").strip(".")


def add_to_deny_list(text: str | None, domain: str, day: str) -> tuple[str, bool, int]:
    """(new file text, added, count). A domain already covered by an entry (itself or a parent domain) is
    not added again; that entry's ``# pkpipe: flagged N, last <day>`` counter is incremented instead."""
    lines = (text or "").splitlines()
    domain = domain.lower()
    for i, line in enumerate(lines):
        entry = _line_domain(line)
        if entry and (domain == entry or domain.endswith("." + entry)):
            m = _COUNT.search(line)
            count = int(m.group(1)) + 1 if m else 1
            lines[i] = _PKPIPE_COMMENT.sub("", line).rstrip() + f"  # pkpipe: flagged {count}, last {day}"
            return "\n".join(lines) + "\n", False, count
    lines.append(f"{domain}  # pkpipe: flagged 1, last {day}")
    return "\n".join(lines) + "\n", True, 1


def fence_for(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text or "")), default=0)
    return "`" * max(3, longest + 1)


def wrap_inert(text: str) -> str:
    fence = fence_for(text)
    body = text if text.endswith("\n") or not text else text + "\n"
    return f"{fence}text\n{body}{fence}\n"


def text_format(text: str) -> dict[str, bool]:
    """How to restore ``text`` byte for byte after :func:`wrap_inert` (which works on ``\\n`` lines and ends
    the block's text with a newline): whether it used CRLF line ends and whether it ended with a newline."""
    return {"crlf": "\r\n" in text, "final_newline": text.endswith("\n")}


def restore_format(text: str, fmt: Any) -> str:
    """Undo the normalisation of :func:`wrap_inert` with the :func:`text_format` stored in the quarantine
    note (no record: the text as it is)."""
    if not isinstance(fmt, dict):
        return text
    if fmt.get("final_newline") is False and text.endswith("\n"):
        text = text[:-1]
    if fmt.get("crlf"):
        text = text.replace("\n", "\r\n")
    return text


def unwrap_inert(block: str) -> str | None:
    """The original text of a block made by :func:`wrap_inert` (None when ``block`` has no such block)."""
    m = re.search(r"^(`{3,})text\n(.*?)^\1\s*$", block, flags=re.DOTALL | re.MULTILINE)
    return m.group(2) if m else None


def _scores(flags: list[dict[str, Any]]) -> dict[str, float]:
    return {
        str(f.get("flag") or f.get("detector")): round(float(f["score"]), 4)
        for f in flags
        if isinstance(f.get("score"), int | float)
    }


def quarantine_front_matter(
    *,
    item_id: str,
    original_path: str | None,
    flags: list[dict[str, Any]],
    run_id: str,
    flagged_at: str,
    url: str = "",
) -> dict[str, Any]:
    """``security_flag`` front matter: flag names, detectors, reason codes and scores (spec 029), never text.
    ``source_url`` is the address of the flagged page (the same address the malicious-URL log holds)."""
    front: dict[str, Any] = {
        "security_flag": {
            "flags": sorted({str(f.get("flag") or f.get("detector")) for f in flags}),
            "detectors": sorted({str(f.get("detector")) for f in flags if f.get("detector")}),
            "reason_codes": [str(f.get("reason_code"))[:80] for f in flags if f.get("reason_code")][:20],
            "run_id": run_id,
            "flagged_at": flagged_at,
        },
        "pkpipe_item": item_id,
        "original_path": original_path,
        "quarantined": True,
    }
    if url:
        front["source_url"] = url[:500]
    if scores := _scores(flags):
        front["security_flag"]["scores"] = scores
    for f in flags:
        if f.get("detector") == "classifier" and isinstance(f.get("detail"), dict):
            front["security_flag"]["classifier"] = f["detail"]  # per model scores and labels, no text
    return front


def evidence_section(flags: list[dict[str, Any]]) -> str:
    """The "Why it was flagged" section of the quarantine note: per model scores and fixed sentences that
    explain them (spec 029). Empty without classifier evidence."""
    from pkpipe.security.evidence import interpret

    for f in flags:
        detail = f.get("detail")
        if f.get("detector") == "classifier" and isinstance(detail, dict) and detail.get("members"):
            rows = [
                "| Model | Kind | Top score | Label | Chunks over threshold |",
                "| --- | --- | --- | --- | --- |",
            ]
            for m in detail["members"]:
                rows.append(
                    f"| {m['model']} | {m['kind']} | {float(m['max_score']):.3f} | {m.get('label', '-')} | "
                    f"{m.get('flagged_chunk_count', 0)} of {m.get('chunks', 0)} |"
                )
            lines = "\n".join(f"- {s}" for s in interpret(detail))
            return f"## Why it was flagged\n\n{chr(10).join(rows)}\n\n{lines}\n\n"
    return ""


def quarantine_body(
    item_id: str, original_text: str | None, url: str = "", flags: list[dict[str, Any]] | None = None
) -> str:
    # inline code keeps the address from rendering as a link (the note is inert); a backtick cannot end it
    source = f"Source: `{url.replace(chr(96), '%60')}`\n\n" if url else ""
    head = (
        f"# Quarantined item {item_id}\n\n{source}"
        "pkpipe flagged this item (see `security_flag`). The original content is kept below in inert form; "
        "do not copy it out of the code block. Release a false positive from the triage page of the UI or "
        "with the app MCP tool `release_quarantine`.\n\n" + evidence_section(flags or [])
    )
    if original_text is None:
        return head + "_No source note: the item came from a URL only._\n"
    return head + wrap_inert(original_text)


def release_candidate(
    *, item_id: str, text: str, flags: list[str], day: str, max_chars: int = 4000
) -> dict[str, Any]:
    """An eval case for ``datasets/security.jsonl`` (spec 009 US3, spec 013 source ``release``): the owner
    released the item, so its text is a benign case the guards flagged. A candidate until the owner moves it
    into the dataset."""
    return {
        "id": f"release-{item_id}-{day}",
        "input": {"text": text[:max_chars]},
        "expected": {"label": "benign", "type": "released_false_positive", "flags": sorted(flags)},
        "source": "release",
        "description": f"owner released quarantined item {item_id} on {day}",
    }
