"""Entities and the code-made verdict of secure fetch (spec 008 FR-005, FR-006, data-model).

The verdict is deterministic code (constitution V): any of URL filter, heuristics, classifier, canary or the
Reader's ``injection_suspected`` raises a security flag. A model never decides to stop, it only reports.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FlagName = Literal["url_filter", "heuristic", "classifier", "canary", "reader"]
FetchStatus = Literal["ok", "blocked", "failed"]
ContentKind = Literal["html", "text", "pdf", "video", "image", "audio", "other"]


class FetchResult(BaseModel):
    """What the web-fetch MCP tool ``fetch_page`` returns (raw, untrusted: only tool-less steps read it)."""

    model_config = ConfigDict(extra="ignore")

    status: FetchStatus
    url: str
    final_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    kind: ContentKind = "other"
    html: str | None = None
    bytes: int = 0
    fetcher: Literal["lightpanda", "chromium"] | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None
    reason: str | None = Field(default=None, description="Reason code of a blocked or failed fetch")


class ReaderExtract(BaseModel):
    """Output of the Reader session (no tools): the only page-derived structure that leaves secure fetch."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=300)
    summary: str = Field(description="At most 200 words")
    facts: list[str] = Field(default_factory=list, description="Short factual bullets")
    links: list[str] = Field(default_factory=list, description="Relevant outbound links")
    injection_suspected: bool
    evidence: str | None = Field(default=None, description="Quoted suspicious text")


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["clean", "flagged", "failed"]
    clean: bool
    flags: list[FlagName] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list, description="Reason codes, never page text")
    flag_reasons: dict[str, str] = Field(default_factory=dict, description="First reason code per flag")
    scores: dict[str, Any] = Field(default_factory=dict, description="Per-detector scores")


def security_json_schemas() -> dict[str, dict[str, Any]]:
    """Exchanged secure-fetch structures for ``pkpipe schema`` (spec 008 data-model)."""
    return {
        "fetch-result": FetchResult.model_json_schema(),
        "reader-extract": ReaderExtract.model_json_schema(),
        "verdict": Verdict.model_json_schema(),
    }


def evaluate(
    *,
    url_check: dict[str, Any] | None,
    fetch: dict[str, Any] | None,
    heuristics: dict[str, Any] | None,
    classifier: dict[str, Any] | None,
    reader: dict[str, Any] | None,
    heuristic_threshold: float = 1.0,
    classifier_threshold: float = 0.5,
    classifier_required: bool = True,
) -> Verdict:
    """Combine every detector into one verdict. Missing parts mean the step did not run (an earlier stop)."""
    flags: list[str] = []
    first: dict[str, str] = {}
    reasons: list[str] = []
    scores: dict[str, Any] = {}
    failed = False

    def flag(name: str, reason: str) -> None:
        if name not in flags:
            flags.append(name)
            first[name] = reason
        reasons.append(reason)

    if url_check is not None and not url_check.get("allowed", False):
        if url_check.get("flag"):
            flag("url_filter", str(url_check.get("reason")))
        else:
            failed = True
            reasons.append(str(url_check.get("reason") or "url_rejected"))
    if fetch is not None:
        status = fetch.get("status")
        if status == "blocked":
            flag("url_filter", str(fetch.get("reason") or "blocked"))
        elif status == "failed":
            failed = True
            reasons.append(str(fetch.get("reason") or "fetch_failed"))
        if fetch.get("fetcher"):
            scores["fetcher"] = fetch["fetcher"]
            scores["fallback_used"] = bool(fetch.get("fallback_used"))
    if heuristics is not None:
        score = float(heuristics.get("score") or 0.0)
        scores["heuristic"] = score
        if score >= heuristic_threshold:
            flag("heuristic", "heuristic:" + ",".join(heuristics.get("matches") or []))
    if classifier is not None:
        if classifier.get("available"):
            score = float(classifier.get("max_score") or 0.0)
            scores["classifier"] = score
            if score >= classifier_threshold:
                flag("classifier", "classifier_threshold")
        elif classifier_required:
            flag("classifier", "classifier_unavailable")  # fail closed
        else:
            scores["classifier"] = None
    if reader is not None:
        if reader.get("canary_leaked"):
            flag("canary", "canary_leak")
        if reader.get("injection_suspected"):
            flag("reader", "injection_suspected")
    if flags:
        return Verdict(
            status="flagged", clean=False, flags=flags, reasons=reasons, flag_reasons=first, scores=scores
        )
    if failed:
        return Verdict(status="failed", clean=False, reasons=reasons, scores=scores)
    return Verdict(status="clean", clean=True, reasons=reasons, scores=scores)
