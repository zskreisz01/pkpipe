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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
import jsonschema
from opentelemetry import trace
from opentelemetry.trace import Span, StatusCode

from pkpipe.engine.config_models import ModelProfile, ModelsConfig
from pkpipe.ports import ContextOverflow, ModelError, ModelUnavailable, SchemaError

THINK = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL)
_CONTEXT_HINTS = (
    "context length",
    "context_length",
    "context window",
    "maximum context",
    "n_ctx",
    "too many tokens",
)


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
            api_key=resolve_value(p.api_key, environ) if p.api_key else None,
            fallback=p.fallback,
        )
    except ProfileError as exc:
        raise ProfileError(f"model profile '{name}': {exc}") from exc


def extract_json_object(text: str) -> Any:
    """Strip ``<think>`` blocks, then parse the first JSON object in the text (also inside prose/fences)."""
    text = THINK.sub("", text).strip()
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
    ):
        self.models = (
            models if isinstance(models, ModelsConfig) else ModelsConfig.model_validate(dict(models))
        )
        self.environ = os.environ if environ is None else environ
        self._client = client or httpx.Client()
        self._tracer = tracer or trace.get_tracer("pkpipe.model")
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
                result = self._call_profile(resolved, system, user, json_schema, used_fallback=len(seen) > 1)
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
    def _event_on_current(self, name: str, attrs: dict[str, Any]) -> None:
        span = trace.get_current_span()
        if span.is_recording():
            span.add_event(name, attrs)

    def _call_profile(
        self, p: ResolvedProfile, system: str, user: str, schema: dict[str, Any] | None, used_fallback: bool
    ) -> Completion:
        with self._tracer.start_as_current_span(f"model.complete {p.name}") as span:
            span.set_attribute("openinference.span.kind", "LLM")
            span.set_attribute("llm.model_name", p.model)
            span.set_attribute("llm.provider", "openai-compatible")
            span.set_attribute("pkpipe.model.profile", p.name)
            span.set_attribute("pkpipe.model.used_fallback", used_fallback)
            started = time.monotonic()
            try:
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
            content, usage, finish = self._request(p, messages, schema)
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
                    {"role": "assistant", "content": THINK.sub("", content).strip()[:4000]},
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
            return {"text": THINK.sub("", content).strip()}
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
                f"{p.base_url}/chat/completions", json=body, headers=headers, timeout=p.timeout_s
            )
        except httpx.HTTPError as exc:
            raise ModelUnavailable(f"{p.base_url}: {type(exc).__name__}: {exc}") from exc
        if resp.status_code >= 500 or resp.status_code in (404, 408, 429):
            raise ModelUnavailable(f"{p.base_url}: HTTP {resp.status_code}: {resp.text[:300]}")
        if resp.status_code >= 400:
            text = resp.text
            if any(h in text.lower() for h in _CONTEXT_HINTS):
                raise ContextOverflow(f"prompt does not fit the context window of '{p.model}': {text[:300]}")
            raise ModelError(f"HTTP {resp.status_code} from {p.base_url}: {text[:300]}")
        try:
            payload = resp.json()
            choice = payload["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelUnavailable(f"{p.base_url}: malformed chat completion response") from exc
        content = message.get("content") or ""
        if not THINK.sub("", content).strip():
            # Qwen3.x on LM Studio may put the whole answer into reasoning_content with an empty content.
            content = message.get("reasoning_content") or message.get("reasoning") or content
        return content, payload.get("usage") or {}, choice.get("finish_reason")
