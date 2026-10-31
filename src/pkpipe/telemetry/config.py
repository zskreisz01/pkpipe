"""Telemetry settings from ``OTEL_*`` environment variables (FR-001) and the content-capture policy."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

# Data classes whose content must never reach a trace backend, whatever the flow config says (FR-004).
PRIVATE_DATA_CLASSES = frozenset({"journal", "context"})


@dataclass(frozen=True)
class TelemetryConfig:
    """Resolved from ``OTEL_*`` variables. Only OTLP over HTTP is exported; ``console`` is a dev aid."""

    enabled: bool = True
    service_name: str = "pkpipe"
    endpoint: str | None = None
    traces_endpoint: str | None = None
    metrics_endpoint: str | None = None
    headers: str | None = None
    console: bool = False
    metrics_enabled: bool = True
    metric_interval_ms: int = 15000

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TelemetryConfig:
        e = os.environ if env is None else env
        protocol = e.get("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
        if protocol != "http/protobuf":
            raise ValueError(
                f"OTEL_EXPORTER_OTLP_PROTOCOL={protocol!r} unsupported; pkpipe exports OTLP http/protobuf"
            )
        exporters = {x.strip() for x in e.get("OTEL_TRACES_EXPORTER", "otlp").split(",") if x.strip()}
        unknown = exporters - {"otlp", "console", "none"}
        if unknown:
            raise ValueError(f"OTEL_TRACES_EXPORTER {sorted(unknown)} not supported (otlp, console, none)")
        return cls(
            enabled=e.get("OTEL_SDK_DISABLED", "false").strip().lower() != "true" and "none" not in exporters,
            service_name=e.get("OTEL_SERVICE_NAME", "pkpipe"),
            endpoint=e.get("OTEL_EXPORTER_OTLP_ENDPOINT") or None,
            traces_endpoint=e.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or None,
            metrics_endpoint=e.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT") or None,
            headers=e.get("OTEL_EXPORTER_OTLP_HEADERS") or None,
            console="console" in exporters,
            metrics_enabled=e.get("OTEL_METRICS_EXPORTER", "otlp").strip().lower() != "none",
            metric_interval_ms=int(e.get("OTEL_METRIC_EXPORT_INTERVAL", "15000")),
        )

    @property
    def exports_otlp(self) -> bool:
        return self.enabled and bool(self.endpoint or self.traces_endpoint)


@dataclass(frozen=True)
class ContentPolicy:
    """Decides per flow whether prompt/response content may be attached to spans.

    Default is off. ``capture_flows`` opts flows in; a flow handling a private data class (journal, context)
    is forced off even when listed (FR-004).
    """

    capture_flows: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_config(cls, config: Mapping[str, object] | None) -> ContentPolicy:
        """``{"capture_content": {"flows": ["flow-id", ...]}}`` (``telemetry`` section of pack/overlay)."""
        section = (config or {}).get("capture_content") or {}
        flows = section.get("flows", []) if isinstance(section, Mapping) else []
        return cls(frozenset(flows))

    def capture_for(self, flow_id: str, data_classes: tuple[str, ...] | frozenset[str] = ()) -> bool:
        if PRIVATE_DATA_CLASSES & set(data_classes):
            return False
        return flow_id in self.capture_flows
