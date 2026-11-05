"""Image input (spec 021): the ``vision`` session and the ``code`` functions of the image flows.

The flows ``describe_images`` (the batch loop with the time guard) and ``describe_image_item`` (one image) of
the public pack chain them::

    plan -> next -> [ prepare -> (cache) -> describe (vision, no tools) -> output_guard -> finish ->
                      read -> menu -> suggest (vision profile, text only) -> output_guard -> check ]
                  -> record -> next ... -> finish_batch

Untrusted-content isolation (constitution IV): the image bytes and every text read from them are untrusted.
The ``vision`` session has no tools and no context pack, its answer is JSON validated against
``image_description``, a canary in its system prompt and an ``output_guard`` behind it; its output is in
``compiler.UNVALIDATED_OUTPUT_TYPES`` so a session with tools can never take it as input. The flow handles the
data class ``image``: loopback model profiles only, no ``egress`` tool and no trace content. Flow state and
checkpoints carry digests, sizes and the validated description, never image bytes (those live in an
:class:`~pkpipe.domain.images.ImageBuffer` of the process).
"""

from __future__ import annotations

import base64
import binascii
import contextvars
import hashlib
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import PurePosixPath
from typing import Any

from pydantic import Field, ValidationError

from pkpipe.domain import image_batch as batch
from pkpipe.domain.data_classes import content_free_errors
from pkpipe.domain.images import PREPARED_MEDIA_TYPE, ImageBuffer, ImageDescription
from pkpipe.domain.text_safety import neutralize_text
from pkpipe.engine.config_models import ImagesConfig
from pkpipe.engine.envref import resolve_chain
from pkpipe.ports import ImageError, ImageInput, ImageStorePort, PreparedImage, SchemaError
from pkpipe.security.canary import leaked
from pkpipe.security.urlfilter import Reason, UrlPolicy
from pkpipe.steps.base import (
    StepContext,
    StepError,
    StepExecutor,
    StepResult,
    register_code,
    register_step,
)
from pkpipe.steps.llm import (
    LlmConfig,
    build_prompt,
    check_session_assets,
    guarded_session_errors,
    step_canary,
    with_canary,
)
from pkpipe.steps.secure_fetch import url_policy
from pkpipe.steps.tool import call_tool

log = logging.getLogger("pkpipe.images")

DATA_CLASS = "image"
READ_IMAGE_TOOL = "obsidian:read_image"
#: The ``flow.node`` of the description session: its prompt, schema and profile make the cache version.
DEFAULT_SESSION = "describe_image_item.describe"
#: Ports in ``StepPorts.extra``.
IMAGE_STORE_PORT = "image_store"
IMAGE_BUFFER_PORT = "image_buffer"
IMAGE_PREPARER_PORT = "image_preparer"
#: A public address stands in for DNS: a URL found in an image is only recorded, never fetched here, so it is
#: checked by scheme, credentials, deny-list and private names without a lookup (a fetch resolves it again).
_NO_DNS_ADDRESS = "93.184.216.34"
_READ_CODES = ("not_found", "bad_name", "bad_type", "too_large")
VIEWER_FLAG = "viewer"
#: Item fields that belong to one image of the batch; ``image.budget`` clears them before the next image.
_PER_IMAGE_FIELDS = (
    "description",
    "image_ok",
    "image_failure",
    "image_cached",
    "image_sha",
    "topic",
    "category",
    "flagged",
    "security_flags",
    "security_flag",
    "security_domain",
    "state",
    "processing_status",
    "quarantine",
    "extract",
    "url",
)


class ImageTimeout(StepError):
    """The description of one image took longer than ``images.per_image_timeout_s``: it fails alone."""


class DescriptionInvalid(StepError):
    """The vision model's answer did not satisfy ``image_description`` after the retries."""


# -- configuration and ports ---------------------------------------------------------------------------------
def images_config(ctx: StepContext) -> ImagesConfig:
    return ImagesConfig.model_validate(ctx.snapshot.data.get("images") or {})


def image_store(ctx: StepContext) -> ImageStorePort | None:
    return ctx.ports.extra.get(IMAGE_STORE_PORT)


def image_buffer(ctx: StepContext) -> ImageBuffer:
    return ctx.ports.extra.setdefault(IMAGE_BUFFER_PORT, ImageBuffer())


def session_node(ctx: StepContext, ref: str):
    """The node ``<flow>.<node>`` (or a node of the current flow) of the description session."""
    flow_id, _, node_id = ref.rpartition(".")
    flow = ctx.snapshot.flow(flow_id) if flow_id else ctx.flow
    return flow.node(node_id)


def model_of(ctx: StepContext, profile: str) -> str:
    """The model id the vision profile resolves to now (the description cache is keyed by it)."""
    models = ctx.snapshot.data.get("models") or {}
    raw = str((models.get(profile) or {}).get("model") or profile)
    return resolve_chain(raw, os.environ) or raw


def prompt_version(ctx: StepContext, node) -> str:
    """Digest of what makes a description: the system prompt, the instruction and the schema of the session.
    A changed prompt or schema is a new cache key, so an old description is never served for it."""
    cfg = node.config
    assets = [
        ctx.snapshot.asset(f"prompts/system/{cfg.get('system')}.md"),
        ctx.snapshot.asset(f"instructions/{cfg['instruction']}") if cfg.get("instruction") else None,
        ctx.snapshot.asset(f"schemas/{cfg['output_schema']}.json") if cfg.get("output_schema") else None,
    ]
    parts = [a or "" for a in assets]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:12]


# -- reading and preparing one image -------------------------------------------------------------------------
def image_name(item: dict[str, Any]) -> str:
    """The file name of an image entry: the last segment of its embed target (the vault tool takes a name)."""
    return PurePosixPath(str(item.get("resource") or "")).name


def read_failure_code(exc: BaseException) -> str:
    """The stable code of a refused ``read_image`` call: the exception's own (an in-process vault) or the one
    named in the tool error message (MCP); anything else is ``read_failed``. Never the message itself."""
    code = getattr(exc, "code", None)
    if code in _READ_CODES:
        return str(code)
    match = re.search("|".join(_READ_CODES), str(exc))
    return match.group(0) if match else "read_failed"


def load_prepared(
    ctx: StepContext, cfg: ImagesConfig, item: dict[str, Any]
) -> tuple[PreparedImage | None, str | None]:
    """``(prepared image, None)`` or ``(None, failure code)``. The prepared JPEG goes into the buffer; a
    later call for the same item finds it there (the batch plan prepares every image once)."""
    key = str(item["id"])
    buffer = image_buffer(ctx)
    found = buffer.get(key)
    if found is not None:
        return found, None
    try:
        raw = call_tool(ctx, READ_IMAGE_TOOL, {"name": image_name(item)})
        data = base64.b64decode(str(raw["data_base64"]), validate=True)
    except (binascii.Error, KeyError, TypeError):
        return None, "read_failed"
    except Exception as exc:  # noqa: BLE001 - a refused or failed read is that image's failure, nothing more
        return None, read_failure_code(exc)
    if len(data) > cfg.max_bytes:
        return None, "too_large"
    try:
        preparer = ctx.ports.require(IMAGE_PREPARER_PORT)
        prepared = preparer.prepare(data, max_side=cfg.max_side, max_pixels=cfg.max_pixels)
    except ImageError as exc:
        return None, exc.code
    buffer.put(key, prepared)
    return prepared, None


def _prepare_one(ctx: StepContext, cfg: ImagesConfig, item: dict[str, Any], session: str) -> dict[str, Any]:
    """Read, prepare and look up one image: the output of ``image.prepare`` (digests and sizes only)."""
    node = session_node(ctx, session)
    profile = str(node.config.get("profile") or "default")
    model, version = model_of(ctx, profile), prompt_version(ctx, node)
    with ctx.ports.tracer.tool("image.prepare") as span:
        prepared, failure = load_prepared(ctx, cfg, item)
        if prepared is None:
            span.set("image.failure", failure or "unknown")
            return {"ok": False, "reason": failure, "key": item["id"], "cached": False}
        store = image_store(ctx)
        description = store.cache_get(prepared.sha256, model, version) if store else None
        span.set("image.bytes", len(prepared.data))
        span.set("image.source_bytes", prepared.source_bytes)
        span.set("image.cache_hit", description is not None)
    return {
        "ok": True,
        "reason": None,
        "key": item["id"],
        "sha256": prepared.sha256,
        "width": prepared.width,
        "height": prepared.height,
        "bytes": len(prepared.data),
        "cached": description is not None,
        "description": description,
        "model": model,
        "prompt_version": version,
    }


@register_code("image.prepare", data_class=DATA_CLASS)
def prepare(ctx: StepContext, session: str = DEFAULT_SESSION) -> StepResult:
    """BPMN: read_image, Prepare, Cache hit?. Reads the item's image through the vault tool (``read_image``,
    ``attachments_dir`` read only), removes EXIF, scales to ``images.max_side``, hashes the prepared bytes and
    looks the hash up in the description cache. A failure is a result (``ok: false`` and a stable ``reason``),
    not an exception: the flow sends that image to the owner and goes on."""
    item = ctx.item or {}
    out = _prepare_one(ctx, images_config(ctx), item, session)
    fields: dict[str, Any] = {"image_ok": out["ok"], "image_cached": bool(out.get("cached"))}
    if out["ok"]:
        fields["image_sha"] = out["sha256"]
    else:
        fields["image_failure"] = out["reason"]
    return StepResult(output=out, item=fields)


# -- the batch -----------------------------------------------------------------------------------------------
@register_code("image.handoff")
def image_handoff(
    ctx: StepContext, images: list[dict[str, Any]] | None = None, cycle_started: float | None = None
) -> dict[str, Any]:
    """The item the triage flow hands to the ``describe_images`` flow: the new image entries (in list order)
    and the start of the cycle. ``count`` routes the flow: no image, no batch."""
    entries = [i for i in images or [] if isinstance(i, dict)]
    return {"count": len(entries), "item": {"images": entries, "cycle_started": cycle_started}}


@register_code("image.plan", data_class=DATA_CLASS)
def plan(ctx: StepContext, session: str = DEFAULT_SESSION) -> StepResult:
    """BPMN: Count images that need a model call, Report the estimate. The item holds the image entries
    (``images``) and the start of the cycle. Every image is read and prepared once, so the batch knows which
    ones are cached (free) and which fail without a model call; the seed of the estimate is the median of
    the last runs in the timing table, else ``images.estimate_s``."""
    cfg = images_config(ctx)
    item = ctx.item or {}
    images = [i for i in item.get("images") or [] if isinstance(i, dict) and i.get("id")]
    node = session_node(ctx, session)
    model = model_of(ctx, str(node.config.get("profile") or "default"))
    store = image_store(ctx)
    seed = batch.seed_estimate(store.recent_seconds(model, 50) if store else [], cfg.estimate_s)
    to_describe = 0
    scans: dict[str, dict[str, Any]] = {}
    for entry in images:
        scan = _prepare_one(ctx, cfg, entry, session)
        scans[str(entry["id"])] = {"ok": scan["ok"], "cached": scan["cached"], "sha": scan.get("sha256", "")}
        if scan["ok"] and not scan["cached"]:
            to_describe += 1
    now = ctx.ports.clock.now()
    started = item.get("cycle_started")
    state = batch.start_batch(
        [str(i["id"]) for i in images],
        started=float(started) if isinstance(started, int | float) else now,
        batch_started=now,
        budget_s=cfg.time_budget_s,
        seed_s=seed,
        to_describe=to_describe,
    )
    state["items"] = {str(i["id"]): i for i in images}
    state["scan"] = scans  # what the plan found per image: record needs it when the image's subflow failed
    state["model"] = model
    message = batch.describe_estimate(
        count=to_describe,
        seed_s=seed,
        budget_s=cfg.time_budget_s,
        elapsed_s=max(0.0, now - state["started"]),
    )
    profile = str(node.config.get("profile") or "default")
    hint = (
        f"Image phase: model profile '{profile}' ({model}) takes over from the text profile; the text model "
        "is not needed any more and may be unloaded now (the model is switched once, never interleaved)."
    )
    log.info(message)
    log.info(hint)
    out = {
        "count": len(images),
        "to_describe": to_describe,
        "seed_s": seed,
        "message": message,
        "phase_hint": hint,
        "image_profile": profile,
    }
    return StepResult(output=out, item={"batch": state})


@register_code("image.budget", data_class=DATA_CLASS)
def budget(ctx: StepContext) -> StepResult:
    """BPMN: Images left? / expected end < time budget? (FR-014, the time guard). Decides before every image:
    ``continue`` (the image becomes the current item, the fields of the previous one are cleared), ``stop``
    (the budget would be reached: this image and the rest are deferred, untouched in the INBOX list) or
    ``done``."""
    state, decision = batch.next_image((ctx.item or {})["batch"], now=ctx.ports.clock.now())
    out = {
        "decision": decision,
        "estimate_s": round(batch.running_estimate(state), 1),
        "remaining": len(state["queue"]) - state["cursor"],
    }
    if decision != batch.CONTINUE:
        return StepResult(output=out, item={"batch": state})
    entry = dict(state["items"][state["current"]])
    cleared = dict.fromkeys(_PER_IMAGE_FIELDS)
    return StepResult(output=out, item={**cleared, **entry, "batch": state})


def _failure_reason(ctx: StepContext) -> str:
    item = ctx.item or {}
    if item.get("image_failure"):
        return str(item["image_failure"])
    error = ctx.resolve("error", required=False) or {}
    kind = str(error.get("type") or "")
    if kind == "StepFailed":  # the subflow wraps the failing step: its cause is named in the message
        found = re.search(r"failed: ([A-Za-z_]+)", str(error.get("message") or ""))
        kind = found.group(1) if found else kind
    return kind or "not_described"


@register_code("image.record", data_class=DATA_CLASS)
def record(ctx: StepContext) -> StepResult:
    """BPMN: Update estimate. The outcome of the current image goes into the batch; a model call (also a
    failed or timed-out one) adds its seconds to the running estimate and to the timing table."""
    item = ctx.item or {}
    state = item["batch"]
    # A failed image subflow (a timeout, an invalid answer) merges no item update: what happened before the
    # failure is known from the plan's scan of the same image, so its model time still counts.
    scan = (state.get("scan") or {}).get(str(item.get("id"))) or {}
    prepared_ok = item["image_ok"] is True if item.get("image_ok") is not None else bool(scan.get("ok"))
    cached = bool(item["image_cached"]) if item.get("image_cached") is not None else bool(scan.get("cached"))
    sha = str(item.get("image_sha") or scan.get("sha") or "")
    if item.get("state") == "quarantined" or item.get("flagged"):
        status, used = batch.QUARANTINED, prepared_ok and not cached
    elif item.get("category") and prepared_ok:
        status, used = (batch.CACHED if cached else batch.DESCRIBED), not cached
    else:
        # a read or decode failure took no model time; a failed or timed-out model call did, and counts
        status, used = batch.FAILED, prepared_ok and not cached
    result: dict[str, Any] = {"resource": item.get("resource"), "kind": "image"}
    if status in (batch.DESCRIBED, batch.CACHED):
        result.update(category=item["category"], topic=_one_line(item.get("topic") or ""))
    elif status == batch.FAILED:
        result["reason"] = _failure_reason(ctx)
    now = ctx.ports.clock.now()
    new = batch.record_image(state, now=now, status=status, used_model=used, result=result)
    store = image_store(ctx)
    if used and store is not None and sha:
        store.record_timing(ctx.run.run_id, sha, str(state.get("model") or ""), new["results"][-1]["seconds"])
    return StepResult(output={"status": status, "used_model": used}, item={"batch": new})


def _one_line(text: str, limit: int = 200) -> str:
    """The topic as one safe line (no markdown, HTML or URL): it is written into the triage file."""
    return neutralize_text(text, limit)


@register_code("image.finish_batch", data_class=DATA_CLASS)
def finish_batch(ctx: StepContext) -> dict[str, Any]:
    """BPMN: Write batch report. The report (counts, estimates, elapsed, deferred) and the triage rows of
    every image that was processed in this run; a deferred image has no row, so it is first in line again."""
    state = (ctx.item or {})["batch"]
    rep = batch.report(state, now=ctx.ports.clock.now())
    rows = [row for r in state["results"] if (row := _row(r)) is not None]
    return {
        "report": rep,
        "message": batch.describe_report(rep),
        "rows": rows,
        "count": len(rows),
        "deferred": list(state["deferred"]),
    }


def _row(result: dict[str, Any]) -> dict[str, Any] | None:
    """The triage row of one processed image. ``needs_owner`` only for a failed or flagged image; the topic
    column holds the one-line topic (never the visible text, never anything of a flagged image)."""
    item_id = result.get("id")
    resource = result.get("resource")
    if not item_id or not resource:
        return None
    status = result["status"]
    base = {"item_id": item_id, "resource": resource, "kind": "image"}
    if status in (batch.DESCRIBED, batch.CACHED):
        return {
            **base,
            "ai_suggested_category": result["category"],
            "state": "suggested",
            "needs_owner": False,
            "topic": result.get("topic", ""),
        }
    if status == batch.QUARANTINED:
        return {**base, "ai_suggested_category": "none", "state": "quarantined", "needs_owner": True}
    reason = re.sub(r"[^A-Za-z0-9_.:-]", "", str(result.get("reason") or "failed"))[:60]
    return {
        **base,
        "ai_suggested_category": "none",
        "state": "suggested",
        "needs_owner": True,
        "topic": f"not described ({reason})",
    }


# -- after the description -----------------------------------------------------------------------------------
_DESCRIPTION_FIELDS = tuple(ImageDescription.model_fields)


def _lexical_urls(ctx: StepContext, urls: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    """The URLs of a description that pass the URL policy and the deny-list (without a DNS lookup), and the
    flags of those on the deny-list."""
    policy: UrlPolicy = url_policy(ctx)
    allowed: list[str] = []
    flags: list[dict[str, str]] = []
    for url in urls:
        decision = policy.check_url(url, lambda host: [_NO_DNS_ADDRESS])
        if decision.allowed:
            allowed.append(url)
        elif decision.reason == Reason.DENY_LIST:
            # a domain the owner or the flag handler blocked: a security flag. Any other refusal (a screenshot
            # of http://localhost:3000, an ftp address) only drops the URL: it is no reason to quarantine.
            flags.append({"flag": "url_filter", "detector": "url_filter", "reason_code": Reason.DENY_LIST})
    return allowed, flags


@register_code("image.finish", data_class=DATA_CLASS)
def finish(ctx: StepContext, describe: str = "describe", prepare: str = "prepare") -> StepResult:
    """BPMN: Injection suspected / guard flag?, store. Validates the description (the answer of the session
    that passed the output guard, or the cached one), applies the URL policy to ``urls``, turns a suspected
    injection or a deny-listed URL into a security flag and caches a clean description of this run.

    The category is never taken from the description: ``category_hint`` is stored, nothing reads it."""
    prep = ctx.resolve(prepare, required=False) or {}
    fresh = ctx.resolve(describe, required=False)
    source = fresh if isinstance(fresh, dict) else prep.get("description")
    if not isinstance(source, dict):
        raise DescriptionInvalid("no description to finish")
    try:
        desc = ImageDescription.model_validate({k: v for k, v in source.items() if k in _DESCRIPTION_FIELDS})
    except ValidationError:
        raise DescriptionInvalid("the description does not satisfy the schema") from None
    allowed, url_flags = _lexical_urls(ctx, desc.urls)
    clean = desc.model_dump()
    clean["urls"] = allowed
    flags = list(url_flags)
    if desc.injection_suspected:
        flags.append({"flag": VIEWER_FLAG, "detector": VIEWER_FLAG, "reason_code": "injection_suspected"})
    out = {
        "flagged": bool(flags),
        "urls": allowed,
        "urls_dropped": len(desc.urls) - len(allowed),
        "fresh": isinstance(fresh, dict),
    }
    if flags:
        for f in flags:
            ctx.ports.tracer.security_flag(f["detector"], action="quarantine", reason_code=f["reason_code"])
        item = {
            "flagged": True,
            "security_flags": flags,
            "security_flag": {
                "source": "image.finish",
                "step": ctx.node.id,
                "flags": [f["flag"] for f in flags],
            },
        }
        return StepResult(output=out, item=item)  # nothing of a flagged image reaches the cache or the vault
    store = image_store(ctx)
    if store is not None and out["fresh"] and prep.get("sha256"):
        store.cache_put(prep["sha256"], prep["model"], prep["prompt_version"], clean)
    return StepResult(output=out, item={"description": clean, "topic": _one_line(clean["topic"])})


def description_note(description: dict[str, Any]) -> str:
    """The text the category session reads about an image: kind, topic, the visible text and the URLs that
    passed the policy. ``category_hint`` is left out on purpose (an injected image could set it)."""
    lines = [f"Image ({description.get('kind', 'other')}): {description.get('topic', '')}"]
    text = str(description.get("visible_text") or "").strip()
    if text:
        lines.append(f"Visible text in the image: {text}")
    urls = [u for u in description.get("urls") or [] if isinstance(u, str)]
    if urls:
        lines.append("Web addresses in the image: " + ", ".join(urls))
    return "\n".join(lines)


# -- the vision session --------------------------------------------------------------------------------------
class VisionConfig(LlmConfig):
    image: str = Field(
        description="Reference to the image.prepare output of this item (its 'key' and 'sha256'); the image "
        "itself comes from the prepared-image buffer, never from the flow state"
    )


def _call_with_timeout(fn, timeout_s: float):
    """Run ``fn`` and give up after ``timeout_s`` seconds (the call keeps running until its own HTTP timeout,
    the step does not wait for it). The OpenTelemetry context travels into the worker thread."""
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pkpipe-image")
    future = pool.submit(contextvars.copy_context().run, fn)
    try:
        return future.result(timeout=max(0.001, timeout_s))
    except FutureTimeout:
        raise ImageTimeout(f"the description took longer than the {timeout_s:.0f} s image timeout") from None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


@register_step
class VisionStep(StepExecutor):
    """The ``describe_image`` session: one image, no tools, no context pack, Reader-style system prompt, JSON
    answer, a canary and an ``output_guard`` behind it. The model only describes; code decides. A hard
    ``max_tokens`` (profile), ``images.retries`` repeated attempts after an invalid answer and the per-image
    timeout bound the cost; then the item stays with the owner."""

    type = "vision"
    Config = VisionConfig

    def check(self, node, config: VisionConfig, snapshot):
        errors, warnings = check_session_assets(node.id, config, snapshot)
        if not config.output_schema:
            errors.append(f"node '{node.id}': a vision session needs an output_schema (JSON only)")
        if config.context:
            errors.append(f"node '{node.id}': a vision session gets no context pack (raw external data only)")
        if not config.canary:
            errors.append(
                f"node '{node.id}': a vision session works on external content: canary must be true"
            )
        profile = (snapshot.data.get("models") or {}).get(config.profile)
        if profile is not None:
            if not profile.get("vision"):
                errors.append(
                    f"node '{node.id}': model profile '{config.profile}' does not declare vision: true"
                )
            images_cfg = ImagesConfig.model_validate(snapshot.data.get("images") or {})
            limit = images_cfg.max_tokens
            if int(profile.get("timeout_s", 120)) > images_cfg.per_image_timeout_s:
                warnings.append(
                    f"node '{node.id}': model profile '{config.profile}' timeout_s "
                    f"{profile.get('timeout_s', 120)} exceeds images.per_image_timeout_s "
                    f"{images_cfg.per_image_timeout_s}: a timed-out image "
                    "keeps occupying the model server until the profile's own timeout"
                )
            tokens = profile.get("max_tokens")
            if not tokens:
                errors.append(
                    f"node '{node.id}': model profile '{config.profile}' needs a max_tokens (a hard "
                    "limit for an image answer)"
                )
            elif tokens > limit:
                errors.append(
                    f"node '{node.id}': model profile '{config.profile}' max_tokens {tokens} exceeds "
                    f"images.max_tokens {limit}"
                )
        return errors, warnings

    def check_flow(self, flow, node, config):
        errors = guarded_session_errors(flow, node, config)
        if DATA_CLASS not in flow.data_classes:
            errors.append(
                f"node '{node.id}' reads an image: the flow must declare data class '{DATA_CLASS}' "
                "(loopback model only, no egress tool, no trace content)"
            )
        return errors

    def run(self, node, config: VisionConfig, ctx: StepContext) -> StepResult:
        cfg = images_config(ctx)
        prep = ctx.resolve(config.image) or {}
        prepared = image_buffer(ctx).get(str(prep.get("key")))
        if prepared is None:  # the process restarted after ``prepare``: read and prepare the image again
            prepared, failure = load_prepared(ctx, cfg, ctx.item or {})
            if prepared is None:
                raise StepError(f"the image cannot be prepared again: {failure}")
        if prepared.sha256 != prep.get("sha256"):
            raise StepError("the image changed since it was prepared")
        system, user, schema = build_prompt(config, ctx)
        system = with_canary(system, config, ctx)
        model = ctx.ports.require("model")
        image = ImageInput(prepared.data, PREPARED_MEDIA_TYPE)
        started = time.monotonic()
        deadline = started + cfg.per_image_timeout_s
        attempts = 1 + cfg.retries
        last: Exception | None = None
        with ctx.ports.tracer.llm(config.profile) as span, content_free_errors(True):
            span.set("pkpipe.model.profile", config.profile)
            span.set("pkpipe.image.bytes", len(prepared.data))
            reply: dict[str, Any] | None = None
            for attempt in range(1, attempts + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ImageTimeout(f"the image timeout of {cfg.per_image_timeout_s} s is used up")
                try:
                    reply = _call_with_timeout(
                        lambda: model.complete(system, user, schema, profile=config.profile, images=[image]),
                        remaining,
                    )
                    desc = ImageDescription.model_validate(reply)
                except (SchemaError, ValidationError) as exc:
                    last, reply = exc, None
                    continue
                span.set("pkpipe.image.attempts", attempt)
                break
            if reply is None:
                span.set("pkpipe.image.attempts", attempts)
                if isinstance(last, SchemaError):
                    # the message and raw text of a schema error quote the model's answer, which quotes the
                    # image: only the type and a fixed text go on
                    raise SchemaError("the answer does not satisfy the schema") from None
                raise DescriptionInvalid("the model's answer did not satisfy the schema") from None
        canary = _canary_of(ctx)
        out = desc.model_dump()
        out["canary_leaked"] = leaked(canary, json.dumps(reply, ensure_ascii=False, default=str))
        out["attempts"] = attempt
        out["seconds"] = round(time.monotonic() - started, 2)
        return StepResult(output=out)


def _canary_of(ctx: StepContext) -> str:
    return step_canary(ctx)
