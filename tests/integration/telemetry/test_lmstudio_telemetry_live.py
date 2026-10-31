"""Spec 003 US1, FR-002, FR-004, SC-002 against the real local model server (LM Studio, needs live).

A model call made with httpx inside a step is traced (LLM span + instrumented HTTP client span, one trace),
throughput is measured, and the prompt/response of a journal flow never reaches the OTLP wire even though the
step records them the way an OpenInference-style adapter would.
"""

from __future__ import annotations

import re
import time

import httpx
import pytest

from pkpipe.security.canary import new_canary
from pkpipe.telemetry import (
    ContentPolicy,
    Telemetry,
    TelemetryConfig,
    capture_content,
    configure,
    flow_span,
    item_context,
    llm_span,
    reset,
    step_span,
)
from tests.support.otel import OtlpReceiver, start_otlp_receiver

pytestmark = pytest.mark.live

THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


@pytest.fixture
def receiver():
    r = start_otlp_receiver()
    yield r
    r.close()


def _chat(lmstudio, prompt: str, tel: Telemetry, max_tokens: int) -> tuple[str, str]:
    with llm_span(lmstudio["model"]) as span:
        span.set_attribute("input.value", prompt)  # what an instrumented adapter records
        started = time.perf_counter()
        r = httpx.post(
            f"{lmstudio['base_url']}/chat/completions",
            json={
                "model": lmstudio["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": max_tokens,
            },
            timeout=180,
        )
        r.raise_for_status()
        body = r.json()
        message = body["choices"][0]["message"]
        answer = THINK.sub("", message.get("content") or "").strip()
        # Qwen3.5 reasoning (LM Studio returns it separately) also echoes the prompt: record it like an
        # OpenInference adapter records output messages, so the scrubbing has real model text to remove.
        reasoning = message.get("reasoning_content") or ""
        span.set_attribute("llm.output_messages.0.message.content", reasoning)
        tokens = body.get("usage", {}).get("completion_tokens", 0)
        tel.metrics.generation(lmstudio["model"], tokens, time.perf_counter() - started)
        span.set_attribute("output.value", answer)
        capture_content(span, prompt=prompt, response=answer, reasoning=reasoning)
        return answer, reasoning


def _run(lmstudio, receiver, flow_id, data_classes, prompt, max_tokens=4096):
    tel = configure(
        Telemetry.create(
            TelemetryConfig(endpoint=receiver.url, metric_interval_ms=60_000),
            policy=ContentPolicy(frozenset({flow_id})),  # opted in: only the data class can force it off
        )
    )
    tel.instrument(langchain=False, httpx=True)
    try:
        with flow_span(flow_id, trigger="manual", config_version="live-1", data_classes=data_classes):
            with item_context("synthetic-item", "misc"), step_span("generate", "Task_Generate"):
                result = _chat(lmstudio, prompt, tel, max_tokens)
        tel.flush()
    finally:
        reset()
        tel.shutdown()
    return result


def test_live_model_call_is_traced_with_http_child_span_and_throughput(lmstudio, receiver):
    """US1.1, FR-002: flow > step > llm > HTTP client span in one trace; tokens_per_second exported."""
    answer, _ = _run(lmstudio, receiver, "learning", (), "Reply with exactly one word: pong")
    assert "pong" in answer.lower()
    spans = {s.name: s for s in receiver.spans()}
    flow, step, llm = spans["flow learning"], spans["step generate"], spans[f"llm {lmstudio['model']}"]
    http = [
        s
        for s in receiver.spans()
        if "POST" in {OtlpReceiver.attrs(s).get(k) for k in ("http.request.method", "http.method")}
    ]
    assert http, sorted(spans)
    assert {s.trace_id for s in (flow, step, llm, http[0])} == {flow.trace_id}
    assert step.parent_span_id == flow.span_id and llm.parent_span_id == step.span_id
    assert http[0].parent_span_id == llm.span_id
    assert OtlpReceiver.attrs(llm)["output.value"].lower().strip(".") == "pong", "opted-in flow keeps content"
    names = {
        m.name
        for req in receiver.metric_requests()
        for rm in req.resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
    }
    assert "pkpipe.tokens_per_second" in names


def test_live_journal_prompt_and_model_echo_never_reach_the_backend(lmstudio, receiver):
    """SC-002, FR-004: the canary is in the prompt and in the model's output, but not on the wire.

    The reasoning budget is short on purpose: Qwen3.5 restates the code in its first reasoning lines, which is
    all this test needs (the final answer may be cut off)."""
    canary = new_canary()
    prompt = f"Dear diary, today I wrote the code {canary}. Repeat that code exactly and nothing else."
    answer, reasoning = _run(lmstudio, receiver, "journal-reflect", ("journal",), prompt, max_tokens=200)
    model_text = f"{answer}\n{reasoning}"
    assert canary in model_text, "precondition: the model output carries the private text"
    blob = b"".join(receiver.trace_bodies + receiver.metric_bodies)
    assert receiver.trace_bodies and canary.encode() not in blob and b"Dear diary" not in blob
    assert not any(line.encode() in blob for line in model_text.splitlines() if len(line) > 20)
    llm = next(s for s in receiver.spans() if s.name.startswith("llm "))
    assert OtlpReceiver.attrs(llm)["pkpipe.content_capture"] is False
