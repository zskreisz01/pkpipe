"""Spec 003 US1/US2, SC-001, SC-002 end to end: a triage run over a synthetic vault is exported over
OTLP (configured only from OTEL_* env) and checked as a trace backend would see it."""

from __future__ import annotations

import pytest

from pkpipe.domain.triage import ItemState, TriageItem, release
from pkpipe.security.canary import new_canary
from pkpipe.telemetry import (
    REQUIRED_STEP_ATTRIBUTES,
    ContentPolicy,
    Telemetry,
    TelemetryConfig,
    configure,
    flow_span,
    item_context,
    record_security_flag,
    reset,
    set_item_category,
    step_span,
)
from tests.support.otel import OtlpReceiver, start_otlp_receiver


@pytest.fixture
def backend():
    r = start_otlp_receiver()
    yield r
    r.close()


def _run_triage(vault, tel):
    """Mini triage flow: classify every INBOX note, release confirmed ones, update the metrics."""
    notes = sorted(p for p in (vault / "0-INBOX").glob("*.md"))
    items = []
    with flow_span("triage", trigger="schedule", config_version="cfg-1"):
        for i, note in enumerate(notes):
            text = note.read_text(encoding="utf-8")
            category = "video" if "video" in text else "tool"
            with item_context(note.stem), step_span("classify", "Task_Classify"):
                set_item_category(category)
                items.append(TriageItem(note.stem, str(note), category, confirm=(i == 0)))
                tel.metrics.item_processed(category)
        with step_span("release", "Task_Release"):
            released, pending = release(items)
            tel.metrics.set_backlog(len(pending))
    return released, pending


def test_triage_run_exports_one_step_span_per_step_with_required_attributes_and_metrics(
    synthetic_vault, backend
):
    """SC-001, US1.1, US2.1."""
    tel = configure(Telemetry.create(TelemetryConfig.from_env({"OTEL_EXPORTER_OTLP_ENDPOINT": backend.url})))
    try:
        released, pending = _run_triage(synthetic_vault, tel)
        tel.flush()
    finally:
        reset()
        tel.shutdown()
    assert len(released) == 1 and len(pending) == 1 and pending[0].state is ItemState.PENDING
    steps = [s for s in backend.spans() if s.name.startswith("step ")]
    assert sorted(s.name for s in steps) == ["step classify", "step classify", "step release"]
    for s in steps:
        a = OtlpReceiver.attrs(s)
        assert all(
            k in a
            for k in REQUIRED_STEP_ATTRIBUTES[:2]
            + ("bpmn.element", "flow.id", "config.version", "run.trigger")
        ), a
    classify = [OtlpReceiver.attrs(s) for s in steps if s.name == "step classify"]
    assert {a["item.category"] for a in classify} == {"tool", "video"}
    assert {a["item.id"] for a in classify} == {"example-tool", "example-video"}
    metrics = {}
    for req in backend.metric_requests():
        for rm in req.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    pts = m.sum.data_points if m.HasField("sum") else m.gauge.data_points
                    for p in pts:
                        key = (m.name, tuple(sorted((kv.key, kv.value.string_value) for kv in p.attributes)))
                        metrics[key] = p.as_int or p.as_double
    assert metrics[("pkpipe.items_total", (("category", "tool"),))] == 1
    assert metrics[("pkpipe.items_total", (("category", "video"),))] == 1
    assert metrics[("pkpipe.backlog_size", ())] == 1


def test_journal_flow_over_otlp_never_leaks_note_text_or_canary(synthetic_vault, backend):
    """SC-002, FR-004, FR-005: journal text + canary stay out of exported bytes; flag event arrives."""
    canary = new_canary()
    journal = synthetic_vault / "0-INBOX" / "journal-entry.md"
    journal.write_text(f"Dear diary, {canary} is my secret.", encoding="utf-8")
    tel = configure(
        Telemetry.create(
            TelemetryConfig(endpoint=backend.url), policy=ContentPolicy(frozenset({"journal-reflect"}))
        )
    )
    try:
        with flow_span("journal-reflect", trigger="manual", config_version="c", data_classes=("journal",)):
            with item_context("journal-entry", "journal"), step_span("reflect", "Task_Reflect") as span:
                span.set_attribute("input.value", journal.read_text(encoding="utf-8"))
                record_security_flag("prompt_guard", action="flag", reason_code="injection_pattern")
                try:
                    raise RuntimeError(journal.read_text(encoding="utf-8"))
                except RuntimeError:
                    pass
        tel.flush()
    finally:
        reset()
        tel.shutdown()
    blob = b"".join(backend.trace_bodies + backend.metric_bodies)
    assert canary.encode() not in blob and b"Dear diary" not in blob
    step = next(s for s in backend.spans() if s.name == "step reflect")
    assert [e.name for e in step.events] == ["security.flag"]
