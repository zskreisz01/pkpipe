"""Spec 003 FR-001 (OTEL_* env, OTLP only) and FR-004 (content policy)."""

import pytest

from pkpipe.telemetry import ContentPolicy, TelemetryConfig


def test_defaults_without_env_export_nothing():
    """FR-001 / docs: with no endpoint configured spans are dropped, not exported."""
    cfg = TelemetryConfig.from_env({})
    assert cfg.enabled and not cfg.exports_otlp and cfg.service_name == "pkpipe"


def test_endpoint_headers_and_service_name_from_standard_variables():
    cfg = TelemetryConfig.from_env(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:6006",
            "OTEL_SERVICE_NAME": "pkpipe-dev",
            "OTEL_EXPORTER_OTLP_HEADERS": "authorization=Bearer x",
        }
    )
    assert cfg.exports_otlp and cfg.endpoint == "http://127.0.0.1:6006"
    assert cfg.service_name == "pkpipe-dev" and cfg.headers == "authorization=Bearer x"


def test_sdk_disabled_and_none_exporter_turn_telemetry_off():
    assert not TelemetryConfig.from_env(
        {"OTEL_SDK_DISABLED": "true", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://x"}
    ).exports_otlp
    assert not TelemetryConfig.from_env(
        {"OTEL_TRACES_EXPORTER": "none", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://x"}
    ).exports_otlp


@pytest.mark.parametrize(
    "env",
    [{"OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}, {"OTEL_TRACES_EXPORTER": "zipkin"}],
)
def test_non_otlp_export_is_rejected(env):
    """FR-001: OTLP only - vendor exporters and other protocols fail loudly."""
    with pytest.raises(ValueError, match="not supported|unsupported"):
        TelemetryConfig.from_env(env)


def test_content_capture_is_off_by_default():
    assert not ContentPolicy().capture_for("learning")
    assert not ContentPolicy.from_config(None).capture_for("learning")


def test_flow_opt_in_and_private_data_classes_force_off():
    """FR-004: journal and context data never captured, even if the flow is listed."""
    policy = ContentPolicy.from_config({"capture_content": {"flows": ["learning", "journal-review"]}})
    assert policy.capture_for("learning")
    assert not policy.capture_for("other")
    assert not policy.capture_for("journal-review", ("journal",))
    assert not policy.capture_for("learning", ("context",))
    assert policy.capture_for("learning", ("public",))


def test_metrics_exporter_can_be_disabled_for_backends_without_otlp_metrics():
    """Phoenix accepts OTLP traces only; OTEL_METRICS_EXPORTER=none keeps the app quiet there."""
    assert TelemetryConfig.from_env({}).metrics_enabled
    assert not TelemetryConfig.from_env({"OTEL_METRICS_EXPORTER": "none"}).metrics_enabled
