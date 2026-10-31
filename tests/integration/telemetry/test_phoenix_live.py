"""Spec 003 T007: Phoenix from deploy/docker-compose.yml receives our OTLP traces (needs Docker)."""

import json
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import pytest

from pkpipe.telemetry import Telemetry, TelemetryConfig, configure, flow_span, item_context, reset, step_span
from tests.conftest import REPO
from tests.support import live

pytestmark = pytest.mark.live("docker")

PORT = 6006


def _port_free() -> bool:
    import socket

    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) != 0


@pytest.fixture(scope="module")
def phoenix():
    if not _port_free():
        pytest.skip(f"port {PORT} already in use (Phoenix probably running)")
    project = f"pkpipe-it-{uuid.uuid4().hex[:6]}"
    compose = ["docker", "compose", "-p", project, "-f", str(REPO / "deploy" / "docker-compose.yml")]
    up = subprocess.run([*compose, "up", "-d", "phoenix"], capture_output=True, text=True, timeout=600)
    if up.returncode != 0:
        pytest.skip(f"cannot start phoenix: {up.stderr[-300:]}")
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=2):
                    break
            except (urllib.error.URLError, OSError):
                time.sleep(1)
        else:
            pytest.fail("phoenix did not become healthy")
        yield f"http://127.0.0.1:{PORT}"
    finally:
        subprocess.run([*compose, "down", "-v"], capture_output=True, timeout=120)


def test_traces_arrive_in_phoenix(phoenix):
    """FR-001, US1: export to the documented local default backend and read the span back via its REST API."""
    cfg = TelemetryConfig.from_env(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": phoenix,
            "OTEL_METRICS_EXPORTER": "none",
            "OTEL_SERVICE_NAME": "pkpipe-live",
        }
    )
    tel = configure(Telemetry.create(cfg))
    marker = uuid.uuid4().hex[:8]
    try:
        with flow_span("learning", trigger="manual", config_version=f"v-{marker}"):
            with item_context(f"item-{marker}", "tool"), step_span("fetch", "Task_Fetch"):
                pass
        tel.flush()
    finally:
        reset()
        tel.shutdown()
    found = False
    deadline = time.time() + 30
    while time.time() < deadline and not found:
        try:
            with urllib.request.urlopen(f"{phoenix}/v1/projects/default/spans?limit=100", timeout=5) as r:
                spans = json.load(r)["data"]
            found = any(s["attributes"].get("item.id") == f"item-{marker}" for s in spans)
        except (urllib.error.URLError, OSError, KeyError, ValueError):
            pass
        if not found:
            time.sleep(1)
    assert found, "step span with our item.id was not found in Phoenix"
    assert live.unavailable_reason("docker") is None
