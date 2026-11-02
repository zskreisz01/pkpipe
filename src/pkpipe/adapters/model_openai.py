"""ModelPort adapter for any OpenAI-compatible chat server (LM Studio, Ollama, vLLM, ...) (spec 002).

Configured only by ``models.json`` profiles (switching servers is a config change, SC-002). Structured output
uses ``response_format: json_schema`` and is always validated locally as well (FR-002). Every call emits an
OpenTelemetry span with OpenInference-style attributes (FR-004).
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
import jsonschema
from opentelemetry import trace
from opentelemetry.trace import Span, StatusCode

from pkpipe.engine.config_models import ModelProfile, ModelsConfig
from pkpipe.ports import ContextOverflow, ModelError, ModelUnavailable, SchemaError

THINK = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL)
# A prompt longer than this many characters per context token certainly overflows the context window, whatever
# the tokenizer (real tokenizers average ~3-4 characters per token for English, fewer for other languages).
_MAX_CHARS_PER_TOKEN = 6
_CONTEXT_HINTS = (
    "context length",
    "context_length",
    "context window",
    "maximum context",
    "n_ctx",
    "too many tokens",
)
# HTTP 4xx bodies that mean "the model is not ready right now" (LM Studio swaps, JIT-loads or TTL-unloads
# models; a request that arrives meanwhile gets HTTP 400 "Model unloaded"): transient, like a 503.
_TRANSIENT_HINTS = ("model unloaded", "model is unloaded", "model is loading", "model not loaded")
#: Scales every profile's ``timeout_s`` (system preflight, docs/operations.md): set by ``pkpipe eval`` and the
#: live-test sampler when the machine is degraded. Read at call time, clamped to [1, 10].
TIMEOUT_MULTIPLIER_ENV = "PKPIPE_MODEL_TIMEOUT_MULTIPLIER"


def timeout_multiplier(environ: Mapping[str, str]) -> float:
    try:
        value = float(environ.get(TIMEOUT_MULTIPLIER_ENV) or 1.0)
    except ValueError:
        return 1.0
    return min(10.0, max(1.0, value))


class ProfileError(ModelError, ValueError):
    """A profile is unknown or references an unset environment variable."""


@dataclass(frozen=True)
class Completion:
    """Validated JSON object plus usage metadata."""

    data: dict[str, Any]
    profile: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_s: float = 0.0
    attempts: int = 1
    raw: str = ""
    used_fallback: bool = False


@dataclass(frozen=True)
class ResolvedProfile:
    name: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int | None
    context: int | None
    timeout_s: int
    schema_retries: int
    retries: int
    retry_backoff_s: float
    api_key: str | None
    fallback: str | None


def resolve_value(value: str, environ: Mapping[str, str]) -> str:
    """Resolve ``env:NAME`` (required) or ``env:NAME|alt`` (``alt`` = another ``env:`` ref or a literal)."""
    if not value.startswith("env:"):
        return value
    name, _, alt = value[4:].partition("|")
    if environ.get(name):
        return environ[name]
    if alt:
        return resolve_value(alt, environ)
    raise ProfileError(f"environment variable '{name}' is not set")


def resolve_profile(name: str, models: ModelsConfig, environ: Mapping[str, str]) -> ResolvedProfile:
    if name not in models.root:
        raise ProfileError(f"unknown model profile '{name}' (have: {', '.join(sorted(models.root))})")
    p: ModelProfile = models.root[name]
    try:
        return ResolvedProfile(
            name=name,
            base_url=resolve_value(p.base_url, environ).rstrip("/"),
            model=resolve_value(p.model, environ),
            temperature=p.temperature,
            max_tokens=p.max_tokens,
            context=p.context,
            timeout_s=p.timeout_s,
            schema_retries=p.schema_retries,
            retries=p.retries,
            retry_backoff_s=p.retry_backoff_s,
            api_key=resolve_value(p.api_key, environ) if p.api_key else None,
            fallback=p.fallback,
        )
    except ProfileError as exc:
        raise ProfileError(f"model profile '{name}': {exc}") from exc


def strip_reasoning(text: str) -> str:
    """Remove ``<think>...</think>`` blocks, an unterminated ``<think>`` tail, and anything before a lone
    ``</think>`` (chat templates that put the opening tag into the prompt emit only the closing one)."""
    text = THINK.sub("", text)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return text.strip()


def extract_json_object(text: str) -> Any:
    """Strip reasoning, then parse the first JSON object in the text (also inside prose/fences)."""
    text = strip_reasoning(text)
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        return obj
    raise ValueError("no JSON object found in the model reply")


class OpenAIModel:
    """Implements :class:`pkpipe.ports.ModelPort` against ``{base_url}/chat/completions``."""

    def __init__(
        self,
        models: ModelsConfig | Mapping[str, Any],
        environ: Mapping[str, str] | None = None,
        client: httpx.Client | None = None,
        tracer: trace.Tracer | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout_multiplier: float | None = None,
    ):
        #: ``None``: ``PKPIPE_MODEL_TIMEOUT_MULTIPLIER`` of ``environ`` at call time (default 1)
        self.timeout_multiplier = timeout_multiplier
        self.models = (
            models if isinstance(models, ModelsConfig) else ModelsConfig.model_validate(dict(models))
        )
        self.environ = os.environ if environ is None else environ
        self._client = client or httpx.Client()
        self._tracer = tracer or trace.get_tracer("pkpipe.model")
        self._sleep = sleep
        self.last: Completion | None = None

    # -- ModelPort -----------------------------------------------------------------------------------
    def complete(
        self,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None = None,
        profile: str = "default",
    ) -> dict[str, Any]:
        return self.complete_full(system, user, json_schema, profile).data

    def complete_full(
        self,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None = None,
        profile: str = "default",
    ) -> Completion:
        if json_schema is not None:
            jsonschema.Draft202012Validator.check_schema(json_schema)
        seen: list[str] = []
        current: str | None = profile
        last_error: ModelUnavailable | None = None
        while current is not None and current not in seen:
            seen.append(current)
            resolved = resolve_profile(current, self.models, self.environ)
            try:
                result = self._call_profile(
                    resolved, system, user, json_schema, fallback_from=seen[-2] if len(seen) > 1 else None
                )
            except ModelUnavailable as exc:
                last_error = exc
                if resolved.fallback is not None:
                    self._event_on_current(
                        "model.fallback", {"from": current, "to": resolved.fallback, "error": str(exc)}
                    )
                current = resolved.fallback
                continue
            self.last = result
            return result
        raise ModelUnavailable(f"no model endpoint reachable (tried: {', '.join(seen)}): {last_error}")

    # -- internals ---------------------------------------------------------------------------------
    def _timeout(self, p: ResolvedProfile) -> float:
        scale = self.timeout_multiplier
        if scale is None:
            scale = timeout_multiplier(self.environ)
        return p.timeout_s * min(10.0, max(1.0, scale))

    def _event_on_current(self, name: str, attrs: dict[str, Any]) -> None:
        span = trace.get_current_span()
        if span.is_recording():
            span.add_event(name, attrs)

    def _call_profile(
        self,
        p: ResolvedProfile,
        system: str,
        user: str,
        schema: dict[str, Any] | None,
        fallback_from: str | None,
    ) -> Completion:
        used_fallback = fallback_from is not None
        with self._tracer.start_as_current_span(f"model.complete {p.name}") as span:
            if fallback_from is not None:
                # Also on the fallback's own span, so the switch is recorded even without a caller span.
                span.add_event("model.fallback", {"from": fallback_from, "to": p.name})
            span.set_attribute("openinference.span.kind", "LLM")
            span.set_attribute("llm.model_name", p.model)
            span.set_attribute("llm.provider", "openai-compatible")
            span.set_attribute("pkpipe.model.profile", p.name)
            span.set_attribute("pkpipe.model.timeout_s", self._timeout(p))
            span.set_attribute("pkpipe.model.used_fallback", used_fallback)
            started = time.monotonic()
            try:
                self._check_context(p, system, user, schema)
                result = self._attempts(p, system, user, schema, span, started, used_fallback)
            except ModelError as exc:
                span.set_status(StatusCode.ERROR, str(exc))
                span.set_attribute("pkpipe.model.error", type(exc).__name__)
                span.set_attribute("pkpipe.model.latency_s", time.monotonic() - started)
                raise
            span.set_attribute("pkpipe.model.latency_s", result.latency_s)
            span.set_attribute("llm.token_count.prompt", result.prompt_tokens or 0)
            span.set_attribute("llm.token_count.completion", result.completion_tokens or 0)
            span.set_attribute(
                "llm.token_count.total", (result.prompt_tokens or 0) + (result.completion_tokens or 0)
            )
            span.set_attribute("pkpipe.model.attempts", result.attempts)
            return result

    @staticmethod
    def _check_context(p: ResolvedProfile, system: str, user: str, schema: dict[str, Any] | None) -> None:
        """Refuse a prompt that cannot fit ``context`` instead of letting the server truncate it silently."""
        if not p.context:
            return
        chars = len(system) + len(user) + (len(json.dumps(schema)) if schema is not None else 0)
        if chars > p.context * _MAX_CHARS_PER_TOKEN:
            raise ContextOverflow(
                f"prompt of {chars} characters cannot fit the {p.context}-token context of profile "
                f"'{p.name}' ('{p.model}'); split or shorten the input"
            )

    def _attempts(
        self,
        p: ResolvedProfile,
        system: str,
        user: str,
        schema: dict[str, Any] | None,
        span: Span,
        started: float,
        used_fallback: bool,
    ) -> Completion:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        prompt_tokens = completion_tokens = 0
        have_usage = False
        last_err = ""
        last_raw = ""
        for attempt in range(1, p.schema_retries + 2):
            content, usage, finish = self._request_retrying(p, messages, schema, span)
            if usage:
                have_usage = True
                prompt_tokens += usage.get("prompt_tokens") or 0
                completion_tokens += usage.get("completion_tokens") or 0
            last_raw = content
            try:
                data = self._parse(content, schema, finish)
            except SchemaError as exc:
                last_err = str(exc)
                span.add_event("model.schema_error", {"attempt": attempt, "error": last_err})
                messages = [
                    *messages[:2],
                    {"role": "assistant", "content": strip_reasoning(content)[:4000]},
                    {
                        "role": "user",
                        "content": f"Your previous reply was invalid ({last_err}). "
                        "Reply again with only a JSON object that matches the schema.",
                    },
                ]
                continue
            return Completion(
                data=data,
                profile=p.name,
                model=p.model,
                prompt_tokens=prompt_tokens if have_usage else None,
                completion_tokens=completion_tokens if have_usage else None,
                latency_s=time.monotonic() - started,
                attempts=attempt,
                raw=content,
                used_fallback=used_fallback,
            )
        raise SchemaError(
            f"model output invalid after {p.schema_retries + 1} attempt(s): {last_err}", raw=last_raw
        )

    @staticmethod
    def _parse(content: str, schema: dict[str, Any] | None, finish: str | None) -> dict[str, Any]:
        if schema is None:
            if finish == "length":
                raise ModelError("output truncated by max_tokens; raise max_tokens of the profile")
            text = strip_reasoning(content)
            if not text:
                raise ModelError("model returned no answer (empty content, reasoning only)")
            return {"text": text}
        try:
            obj = extract_json_object(content)
        except ValueError as exc:
            hint = " (output truncated by max_tokens)" if finish == "length" else ""
            raise SchemaError(f"{exc}{hint}", raw=content) from exc
        try:
            jsonschema.validate(obj, schema, cls=jsonschema.Draft202012Validator)
        except jsonschema.ValidationError as exc:
            path = "/".join(str(x) for x in exc.absolute_path) or "<root>"
            raise SchemaError(f"schema violation at {path}: {exc.message}", raw=content) from exc
        return obj

    def _request_retrying(
        self, p: ResolvedProfile, messages: list[dict[str, str]], schema: dict[str, Any] | None, span: Span
    ) -> tuple[str, dict[str, Any], str | None]:
        """:meth:`_request` with up to ``retries`` extra tries on a transient failure (connection, timeout,
        5xx, 408/429, "Model unloaded"), waiting ``retry_backoff_s`` * 2^n between them. Each retry is a
        ``model.retry`` span event; when every try fails the last :class:`ModelUnavailable` is raised (and a
        configured fallback profile takes over)."""
        for retry in range(p.retries + 1):
            try:
                return self._request(p, messages, schema)
            except ModelUnavailable as exc:
                if retry >= p.retries:
                    raise
                delay = p.retry_backoff_s * 2**retry
                span.add_event("model.retry", {"retry": retry + 1, "delay_s": delay, "error": str(exc)[:300]})
                self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def _request(
        self, p: ResolvedProfile, messages: list[dict[str, str]], schema: dict[str, Any] | None
    ) -> tuple[str, dict[str, Any], str | None]:
        body: dict[str, Any] = {"model": p.model, "messages": messages, "temperature": p.temperature}
        if p.max_tokens:
            body["max_tokens"] = p.max_tokens
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "pkpipe_output", "strict": True, "schema": schema},
            }
        headers = {"Authorization": f"Bearer {p.api_key or 'not-needed'}"}
        try:
            resp = self._client.post(
                f"{p.base_url}/chat/completions", json=body, headers=headers, timeout=self._timeout(p)
            )
        except httpx.HTTPError as exc:
            raise ModelUnavailable(f"{p.base_url}: {type(exc).__name__}: {exc}") from exc
        if resp.status_code >= 500 or resp.status_code in (404, 408, 429):
            raise ModelUnavailable(f"{p.base_url}: HTTP {resp.status_code}: {resp.text[:300]}")
        if resp.status_code >= 400:
            text = resp.text
            if any(h in text.lower() for h in _CONTEXT_HINTS):
                raise ContextOverflow(f"prompt does not fit the context window of '{p.model}': {text[:300]}")
            if any(h in text.lower() for h in _TRANSIENT_HINTS):
                raise ModelUnavailable(f"{p.base_url}: HTTP {resp.status_code}: {text[:300]}")
            raise ModelError(f"HTTP {resp.status_code} from {p.base_url}: {text[:300]}")
        try:
            payload = resp.json()
            choice = payload["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelUnavailable(f"{p.base_url}: malformed chat completion response") from exc
        content = message.get("content") or ""
        finish = choice.get("finish_reason")
        reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
        if not strip_reasoning(content) and reasoning and finish != "length":
            # Qwen3.x on LM Studio with response_format json_schema puts the (constrained) answer into
            # reasoning_content and leaves content empty. Only a reply that is entirely one JSON object is an
            # answer; free reasoning prose (drafts, quoted examples) is never mined for JSON.
            if schema is not None and _is_single_json_object(reasoning):
                content = reasoning
        return content, payload.get("usage") or {}, finish


def _is_single_json_object(text: str) -> bool:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return isinstance(json.loads(text), dict)
    except ValueError:
        return False
