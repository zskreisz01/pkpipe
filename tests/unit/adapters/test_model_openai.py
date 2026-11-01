"""Spec 002: OpenAIModel against an httpx MockTransport (transport only; the adapter is the real unit)."""

from __future__ import annotations

import json

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pkpipe.adapters.model_openai import (
    OpenAIModel,
    ProfileError,
    extract_json_object,
    resolve_profile,
    resolve_value,
)
from pkpipe.engine.config_models import ModelsConfig
from pkpipe.ports import ContextOverflow, ModelPort, ModelUnavailable, SchemaError

SCHEMA = {
    "type": "object",
    "properties": {"category": {"enum": ["tool", "other"]}, "score": {"type": "number"}},
    "required": ["category"],
    "additionalProperties": False,
}
MODELS = {
    "default": {"base_url": "http://primary.invalid/v1", "model": "m-a", "fallback": "backup"},
    "backup": {"base_url": "http://backup.invalid/v1", "model": "m-b"},
    "judge": {"base_url": "env:JBASE", "model": "env:JMODEL|env:MODEL|m-default", "temperature": 0.0},
}


def reply(content, usage=(10, 5), finish="stop", **extra):
    return {
        "choices": [{"message": {"role": "assistant", "content": content, **extra}, "finish_reason": finish}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
    }


def make(handler, models=None, environ=None, tracer=None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAIModel(models or MODELS, environ=environ or {}, client=client, tracer=tracer)


def test_adapter_satisfies_port():
    assert isinstance(make(lambda r: httpx.Response(200, json=reply("{}"))), ModelPort)


def test_sends_json_schema_response_format_and_validates():  # FR-002, US1.1
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json=reply('{"category": "tool", "score": 0.5}'))

    out = make(handler).complete("sys", "usr", SCHEMA)
    assert out == {"category": "tool", "score": 0.5}
    body = seen["body"]
    assert seen["url"] == "http://primary.invalid/v1/chat/completions"
    assert body["model"] == "m-a" and body["temperature"] == 0.2
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_no_schema_returns_text_without_response_format():
    def handler(request):
        assert "response_format" not in json.loads(request.content)
        return httpx.Response(200, json=reply("<think>hmm</think>pong"))

    assert make(handler).complete("s", "u") == {"text": "pong"}


def test_text_around_json_and_think_block_is_tolerated():  # edge case: text around the JSON
    content = (
        '<think>I should {answer} now</think>Sure! Here you go:\n```json\n{"category": "other"}\n```\nBye'
    )
    out = make(lambda r: httpx.Response(200, json=reply(content))).complete("s", "u", SCHEMA)
    assert out == {"category": "other"}


def test_answer_in_reasoning_content_when_content_empty():
    """Qwen3.x on LM Studio can leave content empty and put the answer into reasoning_content."""
    resp = reply("", reasoning_content='{"category": "tool"}')
    assert make(lambda r: httpx.Response(200, json=resp)).complete("s", "u", SCHEMA) == {"category": "tool"}


@pytest.mark.parametrize(
    "content, fragment",
    [
        ("no json here", "no JSON object"),
        ('{"category": "bogus"}', "schema violation at category"),
        ('{"score": 1}', "schema violation"),
        ('{"category": "tool", "extra": 1}', "schema violation"),
    ],
)
def test_invalid_output_raises_schema_error_after_retry(content, fragment):  # US1.2
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=reply(content))

    with pytest.raises(SchemaError, match=fragment) as exc:
        make(handler).complete("s", "u", SCHEMA)
    assert len(calls) == 2  # default schema_retries=1
    assert exc.value.raw == content
    # the retry tells the model what was wrong and keeps the original prompt
    retry = calls[1]["messages"]
    assert retry[0]["role"] == "system" and retry[-1]["role"] == "user" and "invalid" in retry[-1]["content"]
    assert retry[2] == {"role": "assistant", "content": content}


def test_retry_recovers_and_reports_attempts_and_summed_usage():
    answers = iter(["garbage", '{"category": "tool"}'])
    model = make(lambda r: httpx.Response(200, json=reply(next(answers), usage=(7, 3))))
    assert model.complete("s", "u", SCHEMA) == {"category": "tool"}
    assert model.last.attempts == 2
    assert (model.last.prompt_tokens, model.last.completion_tokens) == (14, 6)


def test_retries_are_configurable():
    models = {"default": {"base_url": "http://x.invalid/v1", "model": "m", "schema_retries": 0}}
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=reply("nope"))

    with pytest.raises(SchemaError):
        make(handler, models).complete("s", "u", SCHEMA)
    assert len(calls) == 1
    models["default"]["schema_retries"] = 3
    calls.clear()
    with pytest.raises(SchemaError):
        make(handler, models).complete("s", "u", SCHEMA)
    assert len(calls) == 4


def test_truncated_output_is_reported_not_silently_accepted():
    resp = reply('{"category": "to', finish="length")
    with pytest.raises(SchemaError, match="truncated"):
        make(lambda r: httpx.Response(200, json=resp)).complete("s", "u", SCHEMA)


@pytest.mark.parametrize(
    "text",
    [
        "This model's maximum context length is 4096 tokens",
        '{"error": {"code": "context_length_exceeded"}}',
        "Trying to keep the first 5000 tokens when context window is 4096",
    ],
)
def test_context_overflow_is_a_typed_error_and_not_retried(text):  # edge case
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, text=text)

    with pytest.raises(ContextOverflow):
        make(handler).complete("s", "u", SCHEMA)
    assert calls == [1]


def test_other_client_errors_are_model_errors_not_unavailable():
    from pkpipe.ports import ModelError

    with pytest.raises(ModelError) as exc:
        make(lambda r: httpx.Response(400, text="bad request")).complete("s", "u")
    assert not isinstance(exc.value, ModelUnavailable)


def test_fallback_used_when_primary_down_and_span_event_recorded():  # US2.2
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    def handler(request):
        if request.url.host == "primary.invalid":
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json=reply('{"category": "tool"}'))

    model = make(handler, tracer=provider.get_tracer("t"))
    assert model.complete("s", "u", SCHEMA) == {"category": "tool"}
    assert model.last.profile == "backup" and model.last.model == "m-b" and model.last.used_fallback
    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert set(spans) == {"model.complete default", "model.complete backup"}
    assert spans["model.complete default"].status.status_code.name == "ERROR"
    assert spans["model.complete backup"].attributes["pkpipe.model.used_fallback"] is True


def test_fallback_event_is_recorded_on_the_failed_primary_span():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    parent = provider.get_tracer("t")

    def handler(request):
        if request.url.host == "primary.invalid":
            return httpx.Response(503, text="loading")
        return httpx.Response(200, json=reply("{}"))

    model = make(handler, tracer=parent)
    with parent.start_as_current_span("flow-step"):
        model.complete("s", "u", {"type": "object"})
    by_name = {s.name: s for s in exporter.get_finished_spans()}
    events = [e for e in by_name["flow-step"].events if e.name == "model.fallback"]
    assert (
        len(events) == 1
        and events[0].attributes["from"] == "default"
        and events[0].attributes["to"] == "backup"
    )


def test_all_endpoints_down_raises_model_unavailable_naming_every_profile():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ModelUnavailable, match="default, backup"):
        make(handler).complete("s", "u")


@pytest.mark.parametrize("status", [500, 503, 404, 429])
def test_server_errors_are_unavailable(status):
    models = {"default": {"base_url": "http://x.invalid/v1", "model": "m"}}
    with pytest.raises(ModelUnavailable, match=str(status)):
        make(lambda r: httpx.Response(status, text="x"), models).complete("s", "u")


def test_malformed_response_is_unavailable():
    models = {"default": {"base_url": "http://x.invalid/v1", "model": "m"}}
    with pytest.raises(ModelUnavailable, match="malformed"):
        make(lambda r: httpx.Response(200, json={"nope": 1}), models).complete("s", "u")


def test_schema_error_does_not_trigger_fallback():
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(200, json=reply("garbage"))

    with pytest.raises(SchemaError):
        make(handler).complete("s", "u", SCHEMA)
    assert set(hosts) == {"primary.invalid"}


def test_span_has_model_tokens_and_latency():  # FR-004
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    model = make(
        lambda r: httpx.Response(200, json=reply("{}", usage=(11, 4))), tracer=provider.get_tracer("t")
    )
    model.complete("s", "u", {"type": "object"})
    (span,) = exporter.get_finished_spans()
    a = span.attributes
    assert a["llm.model_name"] == "m-a" and a["pkpipe.model.profile"] == "default"
    assert a["llm.token_count.prompt"] == 11 and a["llm.token_count.completion"] == 4
    assert a["llm.token_count.total"] == 15 and a["pkpipe.model.latency_s"] >= 0
    assert a["openinference.span.kind"] == "LLM"


def test_failed_call_span_is_marked_error_with_type():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    model = make(lambda r: httpx.Response(200, json=reply("garbage")), tracer=provider.get_tracer("t"))
    with pytest.raises(SchemaError):
        model.complete("s", "u", SCHEMA)
    (span,) = exporter.get_finished_spans()
    assert span.status.status_code.name == "ERROR" and span.attributes["pkpipe.model.error"] == "SchemaError"
    assert [e.name for e in span.events].count("model.schema_error") == 2


def test_invalid_schema_is_rejected_before_any_request():
    def handler(request):
        raise AssertionError("must not be called")

    with pytest.raises(Exception, match="not valid"):
        make(handler).complete("s", "u", {"type": "nonsense"})


def test_profile_selected_by_name_and_env_resolved():  # US2.1
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["model"] = json.loads(request.content)["model"]
        seen["temp"] = json.loads(request.content)["temperature"]
        return httpx.Response(200, json=reply("{}"))

    model = make(handler, environ={"JBASE": "http://judge.invalid:9/v1/", "MODEL": "from-env"})
    model.complete("s", "u", {"type": "object"}, profile="judge")
    assert seen == {"url": "http://judge.invalid:9/v1/chat/completions", "model": "from-env", "temp": 0.0}


def test_unknown_profile_and_unset_env_are_profile_errors():
    models = ModelsConfig.model_validate(MODELS)
    with pytest.raises(ProfileError, match="unknown model profile 'nope'"):
        resolve_profile("nope", models, {})
    with pytest.raises(ProfileError, match="'judge'.*JBASE"):
        resolve_profile("judge", models, {})


@pytest.mark.parametrize(
    "value, env, expected",
    [
        ("plain", {}, "plain"),
        ("env:A", {"A": "1"}, "1"),
        ("env:A|lit", {}, "lit"),
        ("env:A|env:B|lit", {"B": "2"}, "2"),
        ("env:A|env:B|lit", {}, "lit"),
        ("env:A|lit", {"A": ""}, "lit"),
    ],
)
def test_resolve_value(value, env, expected):
    assert resolve_value(value, env) == expected


def test_resolve_value_missing_required_env():
    with pytest.raises(ProfileError, match="'A' is not set"):
        resolve_value("env:A", {})


def test_extract_json_object_picks_first_object_and_skips_broken_braces():
    assert extract_json_object('x { not json } {"a": {"b": 1}} {"c": 2}') == {"a": {"b": 1}}
    with pytest.raises(ValueError):
        extract_json_object('<think>only {"a": 1} thinking')  # an unterminated think block hides everything


def test_lone_closing_think_tag_hides_the_reasoning_before_it():
    """Edge case (text around JSON): templates that put <think> into the prompt emit only </think>; a draft
    object inside that reasoning must not be taken as the answer."""
    content = 'Maybe {"category": "other"}? No, it is a CLI.</think>\n{"category": "tool"}'
    out = make(lambda r: httpx.Response(200, json=reply(content))).complete("s", "u", SCHEMA)
    assert out == {"category": "tool"}


def test_reasoning_prose_is_never_mined_for_a_draft_answer():
    """US1.2: empty content plus free reasoning that quotes a schema-valid draft is not an answer -> retried,
    then SchemaError (never the draft)."""
    resp = reply("", reasoning_content='The user example is {"category": "other"}. Let me think more...')
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=resp)

    with pytest.raises(SchemaError, match="no JSON object"):
        make(handler).complete("s", "u", SCHEMA)
    assert len(calls) == 2


def test_reasoning_cut_by_max_tokens_is_a_truncation_error():
    resp = reply("", finish="length", reasoning_content='{"category": "tool"}')
    with pytest.raises(SchemaError, match="truncated"):
        make(lambda r: httpx.Response(200, json=resp)).complete("s", "u", SCHEMA)


@pytest.mark.parametrize(
    "resp, fragment",
    [
        (reply("", reasoning_content="thinking about pong"), "no answer"),
        (reply("Once upon a ti", finish="length"), "truncated"),
    ],
)
def test_text_mode_never_returns_reasoning_or_truncated_text(resp, fragment):
    """Edge case 'no silent truncation': text replies cut by max_tokens or without an answer are errors."""
    from pkpipe.ports import ModelError

    with pytest.raises(ModelError, match=fragment) as exc:
        make(lambda r: httpx.Response(200, json=resp)).complete("s", "u")
    assert not isinstance(exc.value, ModelUnavailable)


def test_prompt_that_cannot_fit_the_profile_context_is_refused_before_sending():
    """Edge case: context overflow -> typed ContextOverflow, no request (the server could truncate it
    silently), no fallback; a prompt that fits is sent."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    models = {
        "default": {"base_url": "http://x.invalid/v1", "model": "m", "context": 100, "fallback": "big"},
        "big": {"base_url": "http://big.invalid/v1", "model": "b"},
    }
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(200, json=reply('{"category": "tool"}'))

    model = make(handler, models, tracer=provider.get_tracer("t"))
    with pytest.raises(ContextOverflow, match="100-token context"):
        model.complete("s", "x" * 700, SCHEMA)
    assert calls == []
    (span,) = exporter.get_finished_spans()
    assert span.attributes["pkpipe.model.error"] == "ContextOverflow"
    assert model.complete("s", "x" * 200, SCHEMA) == {"category": "tool"} and calls == ["x.invalid"]


def test_fallback_event_is_recorded_even_without_a_caller_span():
    """US2.2: the switch to the fallback profile is a span event on the fallback's own model span."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    def handler(request):
        if request.url.host == "primary.invalid":
            raise httpx.ConnectTimeout("timed out")
        return httpx.Response(200, json=reply("{}"))

    make(handler, tracer=provider.get_tracer("t")).complete("s", "u", {"type": "object"})
    backup = next(s for s in exporter.get_finished_spans() if s.name == "model.complete backup")
    (event,) = [e for e in backup.events if e.name == "model.fallback"]
    assert dict(event.attributes) == {"from": "default", "to": "backup"}


# -- transient retries (live eval robustness: LM Studio stalls, swaps and "Model unloaded") ------------------
ONE = {"default": {"base_url": "http://x.invalid/v1", "model": "m", "retries": 2, "retry_backoff_s": 1.5}}


def make_retrying(handler, models=None, tracer=None):
    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = OpenAIModel(models or ONE, environ={}, client=client, tracer=tracer, sleep=sleeps.append)
    return model, sleeps


@pytest.mark.parametrize(
    "failure",
    [
        lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("timed out", request=r)),
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused", request=r)),
        lambda r: httpx.Response(503, text="busy"),
        lambda r: httpx.Response(400, json={"error": "Model unloaded."}),
    ],
    ids=["timeout", "connect", "503", "model-unloaded-400"],
)
def test_transient_failures_are_retried_with_exponential_backoff(failure):
    """Spec 002 FR-003 / spec 013 FR-002: a transient endpoint failure (timeout, connection, 5xx, LM Studio's
    HTTP 400 "Model unloaded") is retried ``retries`` times with backoff before the call (or a judge
    score) fails; the answer after the retry is returned."""
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) <= 2:
            return failure(request)
        return httpx.Response(200, json=reply('{"category": "tool"}'))

    model, sleeps = make_retrying(handler)
    assert model.complete("s", "u", SCHEMA) == {"category": "tool"}
    assert len(calls) == 3 and sleeps == [1.5, 3.0]


def test_retries_exhausted_raise_model_unavailable_then_fallback_takes_over():
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.host == "primary.invalid":
            return httpx.Response(400, text='{"error":"Model unloaded."}')
        return httpx.Response(200, json=reply('{"category": "tool"}'))

    models = {**MODELS, "default": {**MODELS["default"], "retries": 1, "retry_backoff_s": 0.5}}
    model, sleeps = make_retrying(handler, models)
    assert model.complete("s", "u", SCHEMA) == {"category": "tool"}
    assert hosts == ["primary.invalid", "primary.invalid", "backup.invalid"] and sleeps == [0.5]
    with pytest.raises(ModelUnavailable, match="Model unloaded"):
        make_retrying(lambda r: httpx.Response(400, text="Model unloaded."), ONE)[0].complete("s", "u")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, text="bad request"),
        httpx.Response(400, text="the prompt exceeds the context length"),
        httpx.Response(200, json=reply("garbage")),
    ],
    ids=["client-error", "context-overflow", "schema-error"],
)
def test_non_transient_errors_are_never_retried(response):
    """Only transient endpoint failures are retried: a client error, a context overflow and invalid output
    (handled by ``schema_retries``) fail without extra requests or waits."""
    calls = []

    def handler(request):
        calls.append(1)
        return response

    model, sleeps = make_retrying(handler, {"default": {**ONE["default"], "schema_retries": 0}})
    with pytest.raises(Exception) as exc:
        model.complete("s", "u", SCHEMA)
    assert not isinstance(exc.value, ModelUnavailable)
    assert calls == [1] and sleeps == []


def test_retry_is_recorded_as_span_event_and_default_is_no_retry():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, text="busy") if len(calls) == 1 else httpx.Response(200, json=reply("{}"))

    model, _ = make_retrying(handler, tracer=provider.get_tracer("t"))
    model.complete("s", "u", {"type": "object"})
    (span,) = exporter.get_finished_spans()
    events = [e for e in span.events if e.name == "model.retry"]
    assert len(events) == 1 and events[0].attributes["retry"] == 1 and "503" in events[0].attributes["error"]
    calls.clear()
    plain = {"default": {"base_url": "http://x.invalid/v1", "model": "m"}}
    with pytest.raises(ModelUnavailable):
        make_retrying(handler, plain)[0].complete("s", "u")
    assert calls == [1]
