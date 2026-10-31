"""Spec 003 SC-002 (scrubbing exporter), FR-004 and the unreachable-backend edge case."""

import logging

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pkpipe.telemetry.export import ResilientExporter, ScrubbingExporter

CANARY = "JOURNAL-CANARY-91be"


def _provider(exporter):
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exporter))
    return tp, tp.get_tracer("t")


def _emit(tracer, capture):
    try:
        with tracer.start_as_current_span("llm call") as span:
            span.set_attribute("pkpipe.content_capture", capture)
            span.set_attribute("input.value", f"my diary: {CANARY}")
            span.set_attribute("llm.input_messages.0.message.content", CANARY)
            span.set_attribute("llm.model_name", "qwen")
            span.add_event("exception", {"exception.message": CANARY, "exception.type": "ValueError"})
            raise_in_span = False
            if raise_in_span:
                raise ValueError
    finally:
        pass


def test_scrubbing_exporter_removes_content_when_capture_is_off():
    """SC-002: canary in input/output/exception attributes never leaves the process."""
    inner = InMemorySpanExporter()
    tp, tracer = _provider(ScrubbingExporter(inner))
    _emit(tracer, capture=False)
    (span,) = inner.get_finished_spans()
    assert CANARY not in repr(span.attributes) and CANARY not in repr(span.events)
    assert span.attributes["llm.model_name"] == "qwen", "non-content attributes survive"
    assert span.events[0].attributes["exception.type"] == "ValueError"
    tp.shutdown()


def test_scrubbing_exporter_scrubs_spans_that_lack_the_capture_marker():
    """Fail closed: a span nobody stamped is treated as private."""
    inner = InMemorySpanExporter()
    tp, tracer = _provider(ScrubbingExporter(inner))
    with tracer.start_as_current_span("raw") as span:
        span.set_attribute("input.value", CANARY)
    assert CANARY not in repr(inner.get_finished_spans()[0].attributes)
    tp.shutdown()


def test_scrubbing_exporter_keeps_content_when_capture_is_on():
    inner = InMemorySpanExporter()
    tp, tracer = _provider(ScrubbingExporter(inner))
    _emit(tracer, capture=True)
    assert inner.get_finished_spans()[0].attributes["input.value"].endswith(CANARY)
    tp.shutdown()


def test_scrubbing_drops_status_description():
    from opentelemetry.trace import Status, StatusCode

    inner = InMemorySpanExporter()
    tp, tracer = _provider(ScrubbingExporter(inner))
    with tracer.start_as_current_span("s") as span:
        span.set_status(Status(StatusCode.ERROR, f"bad {CANARY}"))
    out = inner.get_finished_spans()[0]
    assert out.status.status_code is StatusCode.ERROR and not out.status.description
    tp.shutdown()


class _Boom(SpanExporter):
    def export(self, spans):
        raise ConnectionError("backend down")


class _Fail(SpanExporter):
    def export(self, spans):
        return SpanExportResult.FAILURE


def test_resilient_exporter_swallows_errors_warns_and_counts(caplog):
    """Edge case: backend unreachable -> app keeps running, drop with warning metric."""
    dropped = []
    tp, tracer = _provider(ResilientExporter(_Boom(), dropped.append))
    with caplog.at_level(logging.WARNING, logger="pkpipe.telemetry"):
        with tracer.start_as_current_span("a"):
            pass
    assert dropped == [1]
    assert "dropping 1 spans" in caplog.text


def test_resilient_exporter_counts_failure_results_too():
    dropped = []
    tp, tracer = _provider(ResilientExporter(_Fail(), dropped.append))
    with tracer.start_as_current_span("a"):
        pass
    assert dropped == [1]


def test_scrubbing_covers_retrieval_reranker_and_tool_payloads():
    """SC-002: every OpenInference payload family is removed, structural attributes are kept."""
    inner = InMemorySpanExporter()
    tp, tracer = _provider(ScrubbingExporter(inner))
    payload = {
        "output.value": CANARY,
        "retrieval.documents.0.document.content": CANARY,
        "reranker.query": CANARY,
        "reranker.input_documents.0.document.content": CANARY,
        "tool.parameters": CANARY,
        "llm.prompt_template.variables": CANARY,
        "metadata": CANARY,
        "pkpipe.content.prompt": CANARY,
    }
    with tracer.start_as_current_span("chain") as span:
        for k, v in payload.items():
            span.set_attribute(k, v)
        span.set_attribute("openinference.span.kind", "RERANKER")
        span.set_attribute("tool.name", "fetch_url")
    out = inner.get_finished_spans()[0]
    assert CANARY not in repr(out.attributes)
    assert out.attributes["openinference.span.kind"] == "RERANKER"
    assert out.attributes["tool.name"] == "fetch_url"
    tp.shutdown()
