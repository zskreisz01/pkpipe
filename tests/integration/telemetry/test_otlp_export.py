"""Spec 003 FR-001, SC-002 and the unreachable-backend edge case against a real local OTLP/HTTP endpoint."""

import socket
import time

import pytest

from pkpipe.telemetry import (
    ContentPolicy,
    Telemetry,
    TelemetryConfig,
    capture_content,
    configure,
    flow_span,
    item_context,
    reset,
    step_span,
)
from tests.support.otel import OtlpReceiver, start_otlp_receiver

CANARY = "JOURNAL-CANARY-c0ffee"


@pytest.fixture
def receiver():
    r = start_otlp_receiver()
    yield r
    r.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_spans_and_metrics_are_exported_over_otlp_http_from_env(receiver):
    """FR-001: configuration purely from OTEL_* variables; OTLP http/protobuf traces and metrics arrive."""
    cfg = TelemetryConfig.from_env(
        {"OTEL_EXPORTER_OTLP_ENDPOINT": receiver.url, "OTEL_SERVICE_NAME": "pkpipe-it"}
    )
    tel = configure(Telemetry.create(cfg))
    try:
        with flow_span("learning", trigger="schedule", config_version="v9"), item_context("i1", "tool"):
            with step_span("fetch", "Task_Fetch"):
                tel.metrics.item_processed("tool")
        tel.flush()
    finally:
        reset()
        tel.shutdown()
    spans = receiver.spans()
    step = next(s for s in spans if s.name == "step fetch")
    attrs = OtlpReceiver.attrs(step)
    assert attrs["flow.id"] == "learning" and attrs["config.version"] == "v9" and attrs["item.id"] == "i1"
    assert receiver.metric_requests(), "metrics exported via OTLP"
    names = {
        m.name
        for req in receiver.metric_requests()
        for rm in req.resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
    }
    assert "pkpipe.items_total" in names
    service = [
        kv.value.string_value
        for kv in receiver.trace_requests()[0].resource_spans[0].resource.attributes
        if kv.key == "service.name"
    ]
    assert service == ["pkpipe-it"]


def test_journal_canary_never_appears_in_exported_bytes(receiver):
    """SC-002: wire-level check - the canary string is absent from every OTLP body of a journal flow."""
    cfg = TelemetryConfig(endpoint=receiver.url)
    tel = configure(Telemetry.create(cfg, policy=ContentPolicy(frozenset({"journal"}))))
    try:
        with pytest.raises(ValueError):
            with flow_span("journal", trigger="manual", config_version="v1", data_classes=("journal",)):
                with step_span("reflect", "Task_Reflect") as span:
                    span.set_attribute("input.value", f"diary {CANARY}")
                    capture_content(prompt=CANARY)
                    raise ValueError(f"failed on {CANARY}")
        tel.flush()
    finally:
        reset()
        tel.shutdown()
    assert receiver.trace_bodies, "something was exported"
    assert not any(CANARY.encode() in b for b in receiver.trace_bodies + receiver.metric_bodies)


def test_backend_unreachable_keeps_app_running_and_counts_drops(caplog):
    """Edge case: closed OTLP port -> flow completes, exporter drops with a warning metric."""
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    cfg = TelemetryConfig(endpoint=f"http://127.0.0.1:{_free_port()}")
    tel = Telemetry.create(cfg, metric_readers=[reader])
    configure(tel)
    try:
        with caplog.at_level("WARNING"):
            with flow_span("learning", trigger="manual", config_version="v1"), step_span("s", "T"):
                result = "app still works"
            tel.tracer_provider.force_flush(timeout_millis=15000)
        deadline = time.time() + 15
        total = 0
        while time.time() < deadline and total < 1:
            data = reader.get_metrics_data()
            total = sum(
                p.value
                for rm in (data.resource_metrics if data else [])
                for sm in rm.scope_metrics
                for m in sm.metrics
                if m.name == "pkpipe.telemetry_export_failures_total"
                for p in m.data.data_points
            )
            time.sleep(0.1)
    finally:
        reset()
        tel.shutdown()
    assert result == "app still works"
    assert total >= 2, "both spans counted as dropped"


def test_metrics_none_exports_traces_only(receiver):
    """OTEL_METRICS_EXPORTER=none: no metric request reaches the backend."""
    cfg = TelemetryConfig.from_env(
        {"OTEL_EXPORTER_OTLP_ENDPOINT": receiver.url, "OTEL_METRICS_EXPORTER": "none"}
    )
    tel = configure(Telemetry.create(cfg))
    try:
        with flow_span("f", trigger="manual", config_version="v"):
            tel.metrics.item_processed("tool")
        tel.flush()
    finally:
        reset()
        tel.shutdown()
    assert receiver.trace_bodies and not receiver.metric_bodies
