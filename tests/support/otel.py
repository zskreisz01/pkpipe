"""Helpers for telemetry tests: in-memory Telemetry and a local OTLP/HTTP receiver."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pkpipe.telemetry import ContentPolicy, Telemetry, TelemetryConfig


@dataclass
class InMemoryOtel:
    telemetry: Telemetry
    exporter: InMemorySpanExporter
    reader: InMemoryMetricReader

    def spans(self, name_prefix: str = ""):
        return [s for s in self.exporter.get_finished_spans() if s.name.startswith(name_prefix)]

    def metric_points(self, name: str) -> list:
        data = self.reader.get_metrics_data()
        points = []
        for rm in data.resource_metrics if data else []:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    if m.name == name:
                        points.extend(m.data.data_points)
        return points

    def metric_value(self, name: str, **attributes) -> float:
        """Sum of counter/gauge points of ``name`` whose attributes include ``attributes``."""
        total = 0.0
        for p in self.metric_points(name):
            if all(p.attributes.get(k) == v for k, v in attributes.items()):
                total += getattr(p, "value", getattr(p, "sum", 0))
        return total


def make_in_memory(policy: ContentPolicy | None = None) -> InMemoryOtel:
    exporter = InMemorySpanExporter()
    reader = InMemoryMetricReader()
    tel = Telemetry.create(TelemetryConfig(), policy=policy, span_exporter=exporter, metric_readers=[reader])
    return InMemoryOtel(tel, exporter, reader)


@dataclass
class OtlpReceiver:
    """Tiny OTLP/HTTP server that decodes protobuf bodies."""

    server: ThreadingHTTPServer
    trace_bodies: list[bytes] = field(default_factory=list)
    metric_bodies: list[bytes] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def trace_requests(self) -> list[ExportTraceServiceRequest]:
        out = []
        for body in self.trace_bodies:
            req = ExportTraceServiceRequest()
            req.ParseFromString(body)
            out.append(req)
        return out

    def metric_requests(self) -> list[ExportMetricsServiceRequest]:
        out = []
        for body in self.metric_bodies:
            req = ExportMetricsServiceRequest()
            req.ParseFromString(body)
            out.append(req)
        return out

    def spans(self):
        return [
            s
            for req in self.trace_requests()
            for rs in req.resource_spans
            for ss in rs.scope_spans
            for s in ss.spans
        ]

    @staticmethod
    def attrs(span) -> dict:
        return {
            kv.key: kv.value.WhichOneof("value") and getattr(kv.value, kv.value.WhichOneof("value"))
            for kv in span.attributes
        }

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def start_otlp_receiver() -> OtlpReceiver:
    box: dict[str, OtlpReceiver] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            box["r"].headers.append({k.lower(): v for k, v in self.headers.items()})
            if self.path.endswith("/v1/traces"):
                box["r"].trace_bodies.append(body)
            elif self.path.endswith("/v1/metrics"):
                box["r"].metric_bodies.append(body)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    box["r"] = OtlpReceiver(server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return box["r"]
