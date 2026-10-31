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


def test_content_policy_rejects_malformed_config_instead_of_guessing():
    """FR-004: a string instead of a list must not turn into a set of single letters."""
    with pytest.raises(ValueError, match="list of flow ids"):
        ContentPolicy.from_config({"capture_content": {"flows": "learning"}})
    with pytest.raises(ValueError, match="object"):
        ContentPolicy.from_config({"capture_content": ["learning"]})


def test_otlp_headers_are_url_decoded_like_the_otel_spec_says():
    """FR-001: ``Authorization=Basic%20<b64>`` (Langfuse) becomes the header value ``Basic <b64>``."""
    from pkpipe.telemetry.runtime import _parse_headers

    assert _parse_headers("Authorization=Basic%20cGs6c2s=,x-team=a") == {
        "authorization": "Basic cGs6c2s=",
        "x-team": "a",
    }
    assert _parse_headers("Authorization=Basic cGs6c2s=") == {"authorization": "Basic cGs6c2s="}
    assert _parse_headers(None) is None


def test_telemetry_json_is_a_validated_config_file_that_feeds_the_policy(pack_dir, overlay_dir, write_json):
    """FR-004 'configurable per flow': telemetry.json is layered and validated like every config file."""
    from pkpipe.engine.config_models import TelemetrySettings, validate_effective
    from pkpipe.engine.registry import load_effective

    write_json(overlay_dir, "telemetry.json", {"capture_content": {"flows": ["learning"]}})
    cfg = load_effective(pack_dir, overlay_dir)
    policy = ContentPolicy.from_config(cfg.data["telemetry"])
    assert policy.capture_for("learning") and not policy.capture_for("learning", ("journal",))
    assert ContentPolicy.from_config(TelemetrySettings().model_dump()).capture_flows == frozenset()
    errors = validate_effective({"telemetry": {"capture_content": {"flows": "learning", "extra": 1}}})
    assert errors and all(e.startswith("telemetry.json") for e in errors)
