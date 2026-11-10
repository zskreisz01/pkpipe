"""Secure fetch steps (spec 008): ``fetch``, ``sanitize`` and ``reader`` step types and the ``code`` functions
``secure_fetch.url_check``, ``secure_fetch.scan`` and ``secure_fetch.verdict``.

The pack flow ``secure_fetch`` chains them::

    url_check -> fetch (web-fetch MCP: Lightpanda, Chromium fallback) -> sanitize -> scan (heuristics +
    classifier) -> read (Reader: no tools, canary, datamarking, JSON) -> verdict (code)

Untrusted-content isolation (constitution IV): ``fetch``, ``sanitize`` and ``reader`` outputs are raw or
quote raw page text, so they are in ``compiler.UNVALIDATED_OUTPUT_TYPES`` and a session with tools can never
declare them as inputs. What leaves secure fetch is the verdict's ``extract`` (schema-validated Reader fields
without the quoted evidence), set on the item only when the verdict is clean.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field, ValidationError

from pkpipe.domain.item_events import ctx_event
from pkpipe.ports import ClassifierUnavailable
from pkpipe.security import heuristics
from pkpipe.security.canary import canary_notice, leaked, new_canary
from pkpipe.security.sanitize import SanitizedPage, sanitize_html, sanitize_text
from pkpipe.security.urlfilter import Reason, UrlPolicy
from pkpipe.security.verdict import FetchResult, ReaderExtract, Verdict, evaluate
from pkpipe.steps.base import (
    StepConfig,
    StepContext,
    StepError,
    StepExecutor,
    StepResult,
    register_code,
    register_step,
)
from pkpipe.steps.llm import LlmConfig, build_prompt, check_session_assets, complete
from pkpipe.steps.tool import check_tool_allowed

#: Port names in ``StepPorts.extra``.
CLASSIFIER_PORT = "classifier"
RESOLVER_PORT = "resolver"
#: The security log of the flag handler (spec 009): its deny-list applies at once, also inside a pinned run.
SECURITY_LOG_PORT = "security_log"
#: Test-only loopback allowance (``(host, port)`` pairs). Only the composition root of a test sets it; it has
#: no config key, so a pack or overlay can never enable it (see ``pkpipe.security.urlfilter``).
TEST_LOOPBACK_PORT = "fetch_test_loopback"

DEFAULT_FETCH_TOOL = "webfetch:fetch_page"
#: Hosts whose pages are video players: only metadata is extracted (spec 008 edge case).
VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "dailymotion.com", "twitch.tv", "tiktok.com")
METADATA_KINDS = ("pdf", "video", "image", "audio", "other")


# -- helpers ------------------------------------------------------------------------------------------------
def url_policy(ctx: StepContext) -> UrlPolicy:
    """The URL policy of the pinned config: ``security.url_filter`` and its deny-list file, plus the domains
    the security-flag handler has deny-listed meanwhile (spec 009)."""
    security = ctx.snapshot.section("security", {}) or {}
    url_filter = security.get("url_filter") or {}
    deny_path = url_filter.get("deny_list")
    deny_text = ctx.snapshot.asset(deny_path) if deny_path else None
    loopback = ctx.ports.extra.get(TEST_LOOPBACK_PORT) or ()
    policy = UrlPolicy.from_config(url_filter, deny_text, test_loopback=loopback)
    log = ctx.ports.extra.get(SECURITY_LOG_PORT)
    if log is not None:  # domains the security-flag handler deny-listed since this config was pinned
        policy = dataclasses.replace(policy, deny=policy.deny | log.denied_domains())
    return policy


def _resolver(ctx: StepContext):
    return ctx.ports.extra.get(RESOLVER_PORT)


def _decision_dict(url: str, decision) -> dict[str, Any]:
    return {"allowed": decision.allowed, "url": url, "reason": decision.reason, "flag": decision.flag}


# -- url_check ----------------------------------------------------------------------------------------------
@register_code("secure_fetch.url_check")
def url_check(ctx: StepContext, url: str | None = None) -> dict[str, Any]:
    """FR-001: scheme, deny-list, private networks (after DNS resolution). ``url`` defaults to the item's
    ``source_url`` (the URL found in a released INBOX note, spec 010), its ``resource`` or the run input's
    ``url``."""
    if url is None:
        url = (
            ctx.resolve("item.source_url", required=False)
            or ctx.resolve("item.resource", required=False)
            or ctx.resolve("input.url", required=False)
        )
    if not isinstance(url, str) or not url.strip():
        return {"allowed": False, "url": url, "reason": Reason.MALFORMED, "flag": False}
    url = url.strip()
    return _decision_dict(url, url_policy(ctx).check_url(url, _resolver(ctx)))


# -- fetch --------------------------------------------------------------------------------------------------
class FetchConfig(StepConfig):
    url: str = Field(description="URL template, normally ${url_check.url}")
    tool: str = Field(default=DEFAULT_FETCH_TOOL, description="web-fetch MCP tool ('<server>:<tool>')")
    max_bytes: int | None = Field(
        default=None, gt=0, description="Tighter limit than security.url_filter.max_bytes"
    )


@register_step
class FetchStep(StepExecutor):
    """FR-002: one fetch through the web-fetch MCP server (Lightpanda, Chromium fallback inside the server).

    The URL policy is checked again here (defense in depth) and on the final URL after redirects; the server
    enforces it on every request. A blocked URL or an oversized page is ``blocked`` (security flag); an
    unreachable server, a timeout after the fallback or an HTTP error is ``failed`` (not a flag)."""

    type = "fetch"
    Config = FetchConfig

    def check(self, node, config: FetchConfig, snapshot):
        err = check_tool_allowed(config.tool, snapshot)
        return ([f"node '{node.id}': {err}"] if err else []), []

    def run(self, node, config: FetchConfig, ctx: StepContext) -> StepResult:
        url = ctx.render(config.url)
        if not isinstance(url, str):
            raise StepError(f"fetch '{node.id}': url must be a string")
        policy = url_policy(ctx)
        decision = policy.check_url(url, _resolver(ctx))
        if not decision.allowed:
            status = "blocked" if decision.flag else "failed"
            return StepResult(output={"status": status, "url": url, "reason": decision.reason})
        err = check_tool_allowed(config.tool, ctx.snapshot)
        if err:
            raise PermissionError(err)
        max_bytes = min(policy.max_bytes, config.max_bytes or policy.max_bytes)
        tools = ctx.ports.require("tools")
        with ctx.ports.tracer.tool(config.tool) as span:
            try:
                raw = tools.call(config.tool, {"url": url, "max_bytes": max_bytes})
                result = FetchResult.model_validate(raw).model_dump()
            except Exception as exc:  # noqa: BLE001 - server down, bad reply: a failed fetch, not a flag
                span.set("fetch.status", "failed")
                return StepResult(
                    output={"status": "failed", "url": url, "reason": "fetch_error:" + type(exc).__name__}
                )
            result["url"] = url
            body = (result.get("html") or "").encode("utf-8", "ignore")
            if result["status"] == "ok" and len(body) > max_bytes:
                result.update(status="blocked", reason=Reason.SIZE, html=None)
            final = result.get("final_url")
            if result["status"] != "blocked" and final and final != url:
                # also after a failure: a redirect to a deny-listed or private target ends in the proxy's
                # refusal, which a browser reports as an HTTP error page
                hop = policy.check_url(final, _resolver(ctx))
                if not hop.allowed and (hop.flag or result["status"] == "ok"):
                    status = "blocked" if hop.flag else "failed"
                    result.update(status=status, reason=hop.reason, html=None)
            for key in ("status", "kind", "fetcher", "reason"):
                if result.get(key) is not None:
                    span.set(f"fetch.{key}", result[key])
            span.set("fetch.fallback", bool(result.get("fallback_used")))
        return StepResult(output=result)


# -- sanitize -----------------------------------------------------------------------------------------------
class SanitizeConfig(StepConfig):
    input: str = Field(default="fetch", description="Reference to the fetch step output")
    max_chars: int = Field(default=24_000, gt=0, description="Visible text passed on (marked when cut)")


def _kind(fetch: dict[str, Any], page: SanitizedPage) -> str:
    kind = fetch.get("kind") or "other"
    if kind != "html":
        return kind
    host = (urlsplit(fetch.get("final_url") or fetch.get("url") or "").hostname or "").lower()
    if page.meta.get("og:type", "").lower().startswith("video") or any(
        host == h or host.endswith("." + h) for h in VIDEO_HOSTS
    ):
        return "video"
    return "html"


def sanitized_output(fetch: dict[str, Any], max_chars: int) -> dict[str, Any]:
    """Output of the ``sanitize`` step for a successful fetch (shared with the security eval)."""
    kind = fetch.get("kind") or "other"
    body = fetch.get("html") or ""
    if kind in ("html", "text") and body.lstrip().startswith("<"):
        page = sanitize_html(body, fetch.get("final_url") or fetch.get("url"), max_chars=max_chars)
    elif kind in ("html", "text"):
        page = sanitize_text(body, max_chars=max_chars)
    else:
        page = SanitizedPage(removed={})
    kind = _kind(fetch, page)
    out = page.as_dict()
    if kind in METADATA_KINDS:  # typed extract with metadata only: no page text goes on
        out.update(text="", links=[])
    if not out["title"]:
        path = urlsplit(fetch.get("final_url") or fetch.get("url") or "").path
        out["title"] = (path.rstrip("/").rsplit("/", 1)[-1] or "")[:300]
    out.update(
        kind=kind,
        url=fetch.get("url"),
        final_url=fetch.get("final_url"),
        content_type=fetch.get("content_type"),
    )
    return out


@register_step
class SanitizeStep(StepExecutor):
    """FR-003: hidden elements, comments, zero-width and Unicode tag characters removed; non-HTML content
    (PDF, video page, image) keeps metadata only."""

    type = "sanitize"
    Config = SanitizeConfig

    def run(self, node, config: SanitizeConfig, ctx: StepContext) -> StepResult:
        fetch = ctx.resolve(config.input)
        if not isinstance(fetch, dict) or fetch.get("status") != "ok":
            raise StepError(f"sanitize '{node.id}': '{config.input}' is not a successful fetch")
        return StepResult(output=sanitized_output(fetch, config.max_chars))


# -- scan ---------------------------------------------------------------------------------------------------
def _member_scores(m: dict[str, Any]) -> dict[str, Any]:
    """The numbers and labels of one classifier model (spec 029): chunk scores, safety label, categories.
    No text."""
    out: dict[str, Any] = {
        "model": m.get("model"),
        "max_score": float(m.get("max_score") or 0.0),
        "chunks": int(m.get("chunks") or 0),
        "scores": [round(float(s), 4) for s in m.get("scores") or []],
    }
    if m.get("label"):
        out["label"] = str(m["label"])
        out["categories"] = [str(c) for c in m.get("categories") or []][:8]
    return out


def scan_page(page: dict[str, Any], classifier: Any | None) -> dict[str, Any]:
    """Heuristics and classifier scores of a sanitized page (shared with the security eval)."""
    meta = " ".join(str(v) for v in (page.get("meta") or {}).values())
    visible = "\n".join(p for p in (page.get("title") or "", meta, page.get("text") or "") if p)
    hidden = page.get("hidden_text") or ""
    removed = page.get("removed") or {}
    found = heuristics.scan(visible, hidden, tag_chars=int(removed.get("tag_chars") or 0))
    result: dict[str, Any]
    if classifier is None:
        result = {"available": False, "reason": "not_configured"}
    else:
        try:
            res = classifier.scan("\n".join(p for p in (visible, hidden) if p))
            result = {
                "available": True,
                "model": res.get("model"),
                "max_score": float(res.get("max_score") or 0.0),
                "chunks": int(res.get("chunks") or 0),
            }
            if isinstance(res.get("members"), list):  # scores per model of a combined classifier
                result["members"] = [_member_scores(m) for m in res["members"]]
            else:
                result["scores"] = [round(float(s), 4) for s in res.get("scores") or []]
        except ClassifierUnavailable as exc:
            result = {"available": False, "reason": "unavailable", "error": str(exc)[:200]}
    return {"heuristics": found.as_dict(), "classifier": result, "removed": removed}


@register_code("secure_fetch.scan")
def scan(ctx: StepContext, input: str = "sanitize") -> dict[str, Any]:
    """FR-003 heuristics and FR-004 classifier (``security.classifier``, chunked). Scores only, no text."""
    return scan_page(ctx.resolve(input), ctx.ports.extra.get(CLASSIFIER_PORT))


# -- reader -------------------------------------------------------------------------------------------------
def reader_system(system: str, canary: str) -> str:
    """The Reader's system prompt with its canary (shared by the step and the eval)."""
    return system + canary_notice(canary)


def reader_reply(reply: dict[str, Any], canary: str) -> dict[str, Any]:
    """Schema-checked Reader fields plus the code-made canary check (on the whole reply, every field)."""
    out = dict(reply) if isinstance(reply, dict) else {"reply": reply}
    out["canary_leaked"] = leaked(canary, json.dumps(reply, ensure_ascii=False, default=str))
    try:
        ReaderExtract.model_validate({k: v for k, v in out.items() if k != "canary_leaked"}, strict=True)
    except ValidationError:
        out["invalid_output"] = True  # never trusted as an extract: the verdict flags it (fail closed)
    return out


@register_step
class ReaderStep(StepExecutor):
    """FR-005: the Reader session. No tools, no context pack, the page only as datamarked input, a per-run
    canary in the system prompt and a JSON-schema output ``{title, summary, facts, links,
    injection_suspected, evidence}``. The model only reports; the verdict code decides."""

    type = "reader"
    Config = LlmConfig

    def check(self, node, config: LlmConfig, snapshot):
        errors, warnings = check_session_assets(node.id, config, snapshot)
        if not config.output_schema:
            errors.append(f"node '{node.id}': a reader needs an output_schema (JSON only)")
        if config.context:
            errors.append(f"node '{node.id}': a reader gets no context pack (raw external text only)")
        return errors, warnings

    def run(self, node, config: LlmConfig, ctx: StepContext) -> StepResult:
        system, user, schema = build_prompt(config, ctx)
        canary = new_canary()
        reply = complete(ctx, config, reader_system(system, canary), user, schema)
        return StepResult(output=reader_reply(reply, canary))


# -- verdict ------------------------------------------------------------------------------------------------
def _extract(page: dict[str, Any] | None, read: dict[str, Any] | None) -> dict[str, Any]:
    """The validated extract that may leave secure fetch: Reader fields without the quoted evidence, or
    metadata only for non-HTML content."""
    page = page or {}
    base = {
        "url": page.get("final_url") or page.get("url"),
        "kind": page.get("kind"),
        "content_type": page.get("content_type"),
    }
    if read is None:
        return {**base, "title": page.get("title") or "", "summary": "", "facts": [], "links": []}
    fields = ReaderExtract.model_validate(
        {k: v for k, v in read.items() if k in ReaderExtract.model_fields}
    ).model_dump()
    return {
        **base,
        "title": fields["title"],
        "summary": fields["summary"],
        "facts": fields["facts"],
        "links": [link for link in fields["links"] if urlsplit(link).scheme in ("http", "https")],
    }


def _flag_dict(name: str, v: Verdict) -> dict[str, Any]:
    """One flag of the verdict as the flag handler reads it: detector, reason code and (classifier) the
    score and the evidence of both models (spec 029)."""
    flag: dict[str, Any] = {"detector": name, "reason_code": v.flag_reasons[name]}
    if name == "classifier" and isinstance(v.scores.get("classifier"), int | float):
        flag["score"] = round(float(v.scores["classifier"]), 4)
        if v.evidence.get("classifier"):
            flag["detail"] = v.evidence["classifier"]
    elif isinstance(v.scores.get(name), int | float):
        flag["score"] = round(float(v.scores[name]), 4)
    return flag


def decide(
    security: dict[str, Any],
    *,
    url_check: dict[str, Any] | None,
    fetch: dict[str, Any] | None,
    scan: dict[str, Any] | None,
    read: dict[str, Any] | None,
    classifier_required: bool | None = None,
    classifier_waived: bool = False,
) -> Verdict:
    """The verdict with the thresholds of ``security.json`` (shared with the security eval)."""
    guard = security.get("classifier") or {}
    review = guard.get("review") or {}
    found = scan or {}
    required = bool(guard.get("required", True)) if classifier_required is None else classifier_required
    return evaluate(
        url_check=url_check,
        fetch=fetch,
        heuristics=found.get("heuristics"),
        classifier=found.get("classifier") if scan is not None else None,
        reader=read,
        heuristic_threshold=float((security.get("heuristics") or {}).get("threshold", 1.0)),
        classifier_threshold=float(guard.get("threshold", 0.5)),
        classifier_required=required,
        review_enabled=bool(review.get("enabled", False)) and review.get("mode", "hold") == "hold",
        classifier_waived=classifier_waived,
        auto_pass=review.get("mode") == "auto_pass",
    )


@register_code("secure_fetch.verdict")
def verdict(
    ctx: StepContext,
    url_check: str = "url_check",
    fetch: str = "fetch",
    sanitize: str = "sanitize",
    scan: str = "scan",
    read: str = "read",
) -> StepResult:
    """FR-006: any of URL filter, heuristics, classifier, canary or ``injection_suspected`` -> security flag.

    Arguments name the nodes (not templates): a node that did not run (an earlier stop) is simply absent."""
    parts = {
        k: ctx.resolve(ref, required=False)
        for k, ref in (
            ("url_check", url_check),
            ("fetch", fetch),
            ("sanitize", sanitize),
            ("scan", scan),
            ("read", read),
        )
    }
    reviews = ctx.ports.extra.get("classifier_reviews")
    waived = bool(reviews and (ctx.item or {}).get("id") and reviews.waived(str(ctx.item["id"])))
    v = decide(
        ctx.snapshot.section("security", {}) or {},
        **{k: parts[k] for k in parts if k != "sanitize"},
        classifier_waived=waived,
    )
    out = v.model_dump()
    item: dict[str, Any] = {"fetch_status": v.status}
    if v.clean:
        out["extract"] = _extract(parts["sanitize"], parts["read"])
        item["extract"] = out["extract"]
    if v.flags:
        flags = [_flag_dict(f, v) for f in v.flags]
        for f in flags:
            ctx.ports.tracer.security_flag(f["detector"], action="flag", reason_code=f["reason_code"])
        item.update(flagged=True, security_flags=flags)
    elif v.auto_passed:
        # Spec 032: let through by the owner's rule; the evidence travels with the item and the log says so.
        ctx.ports.tracer.security_flag(
            "classifier", action="auto_pass", reason_code="classifier_injection_only"
        )
        item.update(
            auto_passed={"reason": "classifier_injection_only", "evidence": v.evidence.get("classifier")}
        )
        ctx_event(
            ctx,
            "auto_passed",
            "passed",
            classifier=v.evidence.get("classifier"),
            url=(ctx.item or {}).get("source_url"),
        )
    elif v.review:
        # Spec 029: held for the owner, not quarantined. The evidence travels with the item; no page text.
        ctx.ports.tracer.security_flag("classifier", action="review", reason_code=v.review)
        item.update(classifier_review={"reason": v.review, "evidence": v.evidence.get("classifier")})
    ctx_event(
        ctx,
        "verdict",
        "review" if v.review else v.status,
        flags=[str(f.get("reason_code") or f.get("detector")) for f in (item.get("security_flags") or [])],
        classifier=v.evidence.get("classifier"),
        url=(ctx.item or {}).get("source_url"),
    )
    return StepResult(output=out, item=item)
