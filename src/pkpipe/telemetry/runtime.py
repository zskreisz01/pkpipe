"""The Telemetry handle: tracer and meter providers, metrics, and instrumentation switches."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

from . import attrs
from .config import ContentPolicy, TelemetryConfig
from .export import ResilientExporter, ScrubbingExporter

log = logging.getLogger("pkpipe.telemetry")

CAPTURE_KEY = otel_context.create_key("pkpipe.content_capture")


class CaptureProcessor(SpanProcessor):
    """Stamps every new span (also OpenInference/httpx/FastAPI ones) with the capture decision of its context.

    Spans outside a pkpipe flow get ``False``: content is private unless a flow explicitly allows it.
    """

    def on_start(self, span, parent_context: Context | None = None) -> None:
        value = otel_context.get_value(CAPTURE_KEY, parent_context)
        span.set_attribute(attrs.CONTENT_CAPTURE, value is True)


class Metrics:
    """The pkpipe metric instruments (docs/operations.md)."""

    def __init__(self, meter: otel_metrics.Meter):
        self._backlog = 0
        self.items = meter.create_counter(attrs.M_ITEMS, description="Items processed, by category")
        self.flags = meter.create_counter(attrs.M_FLAGS, description="Security flags, by detector")
        self.learning_timeouts = meter.create_counter(attrs.M_LEARNING_TIMEOUTS)
        self.step_errors = meter.create_counter(attrs.M_STEP_ERRORS)
        self.export_failures = meter.create_counter(attrs.M_EXPORT_FAILURES)
        self.learning_seconds = meter.create_histogram(attrs.M_LEARNING_SECONDS, unit="s")
        self.tokens_per_second = meter.create_histogram(attrs.M_TOKENS_PER_SECOND, unit="{token}/s")
        meter.create_observable_gauge(
            attrs.M_BACKLOG, callbacks=[self._observe_backlog], description="INBOX backlog"
        )

    def _observe_backlog(self, options):
        yield otel_metrics.Observation(self._backlog)

    def set_backlog(self, size: int) -> None:
        self._backlog = int(size)

    def item_processed(self, category: str, n: int = 1) -> None:
        self.items.add(n, {"category": category})

    def flag(self, detector: str) -> None:
        self.flags.add(1, {"detector": detector})

    def learning_timeout(self) -> None:
        self.learning_timeouts.add(1)

    def step_error(self, step_id: str, error_type: str) -> None:
        self.step_errors.add(1, {attrs.STEP_ID: step_id, "error.type": error_type})


@dataclass
class Telemetry:
    config: TelemetryConfig
    policy: ContentPolicy
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    metrics: Metrics
    _uninstrument: list[Any] = field(default_factory=list)

    @property
    def tracer(self) -> trace.Tracer:
        return self.tracer_provider.get_tracer("pkpipe")

    @classmethod
    def create(
        cls,
        config: TelemetryConfig | None = None,
        *,
        policy: ContentPolicy | None = None,
        span_exporter: SpanExporter | None = None,
        metric_readers: list[MetricReader] | None = None,
    ) -> Telemetry:
        """Build providers without touching OpenTelemetry globals.

        Production: exporters come from ``OTEL_*`` (OTLP http only). Tests inject ``span_exporter`` (an
        in-memory exporter, exported synchronously) and ``metric_readers`` (an in-memory reader).
        """
        config = config or TelemetryConfig.from_env()
        resource = Resource.create({"service.name": config.service_name})
        readers: list[MetricReader] = list(metric_readers or [])
        failures: list[Metrics] = []

        def on_failure(n: int) -> None:
            for m in failures:
                m.export_failures.add(n)

        tp = TracerProvider(resource=resource)
        tp.add_span_processor(CaptureProcessor())
        simple = span_exporter is not None
        inner: list[SpanExporter] = [span_exporter] if span_exporter is not None else []
        if not simple and config.enabled:
            if config.exports_otlp:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

                inner.append(
                    OTLPSpanExporter(
                        endpoint=config.traces_endpoint or f"{(config.endpoint or '').rstrip('/')}/v1/traces",
                        headers=_parse_headers(config.headers),
                        timeout=5,
                    )
                )
                if not metric_readers and config.metrics_enabled:
                    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

                    readers.append(
                        PeriodicExportingMetricReader(
                            OTLPMetricExporter(
                                endpoint=config.metrics_endpoint
                                or f"{(config.endpoint or '').rstrip('/')}/v1/metrics",
                                headers=_parse_headers(config.headers),
                                timeout=5,
                            ),
                            export_interval_millis=config.metric_interval_ms,
                        )
                    )
            if config.console:
                inner.append(ConsoleSpanExporter())
        for exp in inner:
            wrapped = ScrubbingExporter(ResilientExporter(exp, on_failure))
            tp.add_span_processor(SimpleSpanProcessor(wrapped) if simple else BatchSpanProcessor(wrapped))
        mp = MeterProvider(resource=resource, metric_readers=readers)
        metrics = Metrics(mp.get_meter("pkpipe"))
        failures.append(metrics)
        return cls(config, policy or ContentPolicy(), tp, mp, metrics)

    def instrument(self, *, langchain: bool = True, httpx: bool = True, fastapi_app: Any = None) -> None:
        """FR-002: OpenInference for LangChain/LangGraph, OpenTelemetry for httpx and FastAPI.

        LangChain content attributes follow the policy: the OpenInference instrumentor always records them and
        the scrubbing exporter removes them for flows with capture off.
        """
        if langchain:
            from openinference.instrumentation.langchain import LangChainInstrumentor

            inst = LangChainInstrumentor()
            inst.instrument(tracer_provider=self.tracer_provider, skip_dep_check=True)
            self._uninstrument.append(inst.uninstrument)
        if httpx:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            hi = HTTPXClientInstrumentor()
            hi.instrument(tracer_provider=self.tracer_provider, meter_provider=self.meter_provider)
            self._uninstrument.append(hi.uninstrument)
        if fastapi_app is not None:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(
                fastapi_app, tracer_provider=self.tracer_provider, meter_provider=self.meter_provider
            )
            self._uninstrument.append(lambda: FastAPIInstrumentor.uninstrument_app(fastapi_app))

    def shutdown(self) -> None:
        for fn in reversed(self._uninstrument):
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.warning("uninstrument failed")
        self._uninstrument.clear()
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()

    def flush(self) -> None:
        self.tracer_provider.force_flush()
        self.meter_provider.force_flush()


def _parse_headers(raw: str | None) -> dict[str, str] | None:
    if not raw:
        return None
    pairs = (p.split("=", 1) for p in raw.split(",") if "=" in p)
    return {k.strip(): v.strip() for k, v in pairs}


_active: Telemetry | None = None


def configure(telemetry: Telemetry | None = None, **kwargs: Any) -> Telemetry:
    """Make a Telemetry the process-wide one (used by ``step_span`` & co.). Without arguments: from env."""
    global _active
    _active = telemetry or Telemetry.create(**kwargs)
    return _active


def get_telemetry() -> Telemetry:
    """The active Telemetry; an inert one (no exporters) if ``configure`` was never called."""
    global _active
    if _active is None:
        _active = Telemetry.create(TelemetryConfig(enabled=False))
    return _active


def reset() -> None:
    global _active
    _active = None
