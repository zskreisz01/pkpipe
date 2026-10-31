"""The live-service probe against a real local HTTP server started by the test."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests.support import live


@pytest.fixture
def fake_openai_server():
    models = ["some-model"]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/v1/models":
                body = json.dumps({"data": [{"id": m} for m in models]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    live.lmstudio_models.cache_clear()
    yield f"http://127.0.0.1:{server.server_port}/v1", models
    server.shutdown()
    live.lmstudio_models.cache_clear()


def test_probe_reports_available_when_model_is_served(fake_openai_server, monkeypatch):
    url, models = fake_openai_server
    monkeypatch.setenv("PKPIPE_MODEL_BASE_URL", url)
    monkeypatch.setenv("PKPIPE_MODEL_NAME", "some-model")
    assert live.lmstudio_models() == ("some-model",)
    assert live.unavailable_reason("lmstudio") is None


def test_probe_names_the_missing_model(fake_openai_server, monkeypatch):
    url, _ = fake_openai_server
    monkeypatch.setenv("PKPIPE_MODEL_BASE_URL", url)
    monkeypatch.setenv("PKPIPE_MODEL_NAME", "not-loaded")
    reason = live.unavailable_reason("lmstudio")
    assert "not-loaded" in reason and "some-model" in reason


def test_probe_handles_server_without_models_endpoint(monkeypatch, fake_openai_server):
    url, _ = fake_openai_server
    monkeypatch.setenv("PKPIPE_MODEL_BASE_URL", url + "/wrong")
    live.lmstudio_models.cache_clear()
    assert live.lmstudio_models() == ()
