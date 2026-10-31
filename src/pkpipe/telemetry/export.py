"""Exporter wrappers: content scrubbing (FR-004, SC-002) and failure tolerance (spec edge case)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from . import attrs

log = logging.getLogger("pkpipe.telemetry")

_EXCEPTION_PAYLOAD = ("exception.message", "exception.stacktrace")


def _is_content(key: str) -> bool:
    return key.startswith(attrs.CONTENT_PREFIXES)


def scrub(span: ReadableSpan) -> ReadableSpan:
    """Copy of ``span`` without content attributes, exception messages or status descriptions."""
    attributes = {k: v for k, v in (span.attributes or {}).items() if not _is_content(k)}
    events = [
        Event(
            e.name,
            {
                k: v
                for k, v in (e.attributes or {}).items()
                if k not in _EXCEPTION_PAYLOAD and not _is_content(k)
            },
            e.timestamp,
        )
        for e in span.events
    ]
    status = span.status
    if status.description:
        from opentelemetry.trace import Status

        status = Status(status.status_code)
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=attributes,
        events=events,
        links=span.links,
        kind=span.kind,
        instrumentation_scope=span.instrumentation_scope,
        status=status,
        start_time=span.start_time,
        end_time=span.end_time,
    )


class ScrubbingExporter(SpanExporter):
    """Strips content from every span whose ``pkpipe.content_capture`` attribute is not exactly True."""

    def __init__(self, inner: SpanExporter):
        self.inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        out = [s if (s.attributes or {}).get(attrs.CONTENT_CAPTURE) is True else scrub(s) for s in spans]
        return self.inner.export(out)

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)


class ResilientExporter(SpanExporter):
    """Never lets a backend outage reach the app: failures are logged and counted, spans are dropped."""

    def __init__(self, inner: SpanExporter, on_failure: Callable[[int], None] | None = None):
        self.inner = inner
        self.on_failure = on_failure or (lambda n: None)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self.inner.export(spans)
        except Exception as exc:  # noqa: BLE001 - telemetry must never break the app
            log.warning("span export failed (%s); dropping %d spans", type(exc).__name__, len(spans))
            self.on_failure(len(spans))
            return SpanExportResult.FAILURE
        if result is not SpanExportResult.SUCCESS:
            log.warning("span export reported failure; dropped %d spans", len(spans))
            self.on_failure(len(spans))
        return result

    def shutdown(self) -> None:
        try:
            self.inner.shutdown()
        except Exception:  # noqa: BLE001
            log.warning("span exporter shutdown failed")

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            return self.inner.force_flush(timeout_millis)
        except Exception:  # noqa: BLE001
            return False
