"""The live-service probe against a real local HTTP server started by the test."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests.support import live


def _serve(models: list[str]) -> HTTPServer:
    """A local OpenAI-compatible stub that lists ``models`` at /v1/models (404 elsewhere)."""

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
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def fake_openai_server():
    models = ["some-model"]
    server = _serve(models)
    live.served_models.cache_clear()
    yield f"http://127.0.0.1:{server.server_port}/v1", models
    server.shutdown()
    live.served_models.cache_clear()


@pytest.fixture
def one_model_per_port(monkeypatch):
    """A vLLM-like setup: the model under test and the judge, each alone on its own port."""
    subject, judge = _serve(["subject-model"]), _serve(["judge-model"])
    live.served_models.cache_clear()
    monkeypatch.setenv("PKPIPE_MODEL_BASE_URL", f"http://127.0.0.1:{subject.server_port}/v1")
    monkeypatch.setenv("PKPIPE_MODEL_NAME", "subject-model")
    monkeypatch.setenv("PKPIPE_JUDGE_MODEL", "judge-model")
    yield f"http://127.0.0.1:{judge.server_port}/v1"
    subject.shutdown()
    judge.shutdown()
    live.served_models.cache_clear()


def test_judge_probe_lists_models_at_the_judge_url(one_model_per_port, monkeypatch):
    """With PKPIPE_JUDGE_BASE_URL the judge is looked up at its own port, so the judge tests run instead of
    being skipped because the model port does not list the judge."""
    monkeypatch.setenv("PKPIPE_JUDGE_BASE_URL", one_model_per_port + "/")
    assert live.judge_base_url() == one_model_per_port
    assert live.unavailable_reason("lmstudio", "judge") is None


def test_judge_probe_falls_back_to_the_model_url(one_model_per_port, monkeypatch):
    """Without PKPIPE_JUDGE_BASE_URL the judge must be served at the model URL (one server, both models);
    here it is not, and the reason names that URL and what it serves."""
    monkeypatch.delenv("PKPIPE_JUDGE_BASE_URL", raising=False)
    assert live.judge_base_url() == live.model_base_url()
    reason = live.unavailable_reason("judge")
    assert "judge-model" in reason and live.model_base_url() in reason and "subject-model" in reason


def test_judge_probe_reports_an_unreachable_judge_server(one_model_per_port, monkeypatch):
    monkeypatch.setenv("PKPIPE_JUDGE_BASE_URL", "http://127.0.0.1:9/v1")  # discard port, nothing listens
    assert live.unavailable_reason("judge") == "judge server not reachable at http://127.0.0.1:9/v1"


def test_judge_probe_on_one_server_serving_both_models(fake_openai_server, monkeypatch):
    url, models = fake_openai_server
    models.append("judge-model")
    monkeypatch.setenv("PKPIPE_MODEL_BASE_URL", url)
    monkeypatch.setenv("PKPIPE_MODEL_NAME", "some-model")
    monkeypatch.setenv("PKPIPE_JUDGE_MODEL", "judge-model")
    monkeypatch.delenv("PKPIPE_JUDGE_BASE_URL", raising=False)
    assert live.unavailable_reason("judge") is None


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
    live.served_models.cache_clear()
    assert live.lmstudio_models() == ()
