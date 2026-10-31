"""Spec 002 integration: the real httpx adapter against real local HTTP servers started by the test."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pkpipe.adapters.model_openai import OpenAIModel
from pkpipe.ports import ContextOverflow, ModelUnavailable, SchemaError

SCHEMA = {
    "type": "object",
    "properties": {"category": {"enum": ["tool", "other"]}},
    "required": ["category"],
}


class FakeOpenAIServer:
    """Minimal OpenAI-compatible server. ``script`` entries: (status, body) consumed per request."""

    def __init__(self, prefix: str = "/v1"):
        self.requests: list[dict] = []
        self.script: list[tuple[int, object]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                outer.requests.append(
                    {
                        "path": self.path,
                        "body": json.loads(self.rfile.read(length)),
                        "auth": self.headers.get("authorization"),
                    }
                )
                status, body = outer.script.pop(0) if outer.script else (200, chat('{"category": "tool"}'))
                data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}{prefix}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def chat(content: str, **message) -> dict:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content, **message}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }


@pytest.fixture
def server():
    s = FakeOpenAIServer()
    yield s
    s.close()


@pytest.fixture
def dead_url():
    """A local URL where nothing listens (port was just released)."""
    s = FakeOpenAIServer()
    url = s.url
    s.close()
    return url


def test_roundtrip_over_real_http(server):
    """US1.1 / FR-002: request carries json_schema response_format, auth header and validated result."""
    model = OpenAIModel(
        {"default": {"base_url": server.url, "model": "m1", "api_key": "env:KEY"}}, environ={"KEY": "sk-test"}
    )
    assert model.complete("sys", "usr", SCHEMA) == {"category": "tool"}
    (req,) = server.requests
    assert req["path"] == "/v1/chat/completions" and req["auth"] == "Bearer sk-test"
    assert req["body"]["response_format"]["json_schema"]["schema"] == SCHEMA
    assert model.last.prompt_tokens == 3 and model.last.completion_tokens == 2


def test_invalid_then_valid_retry_over_http(server):
    server.script = [(200, chat("<think>x</think> not json")), (200, chat('ok {"category": "other"}'))]
    model = OpenAIModel({"default": {"base_url": server.url, "model": "m"}}, environ={})
    assert model.complete("s", "u", SCHEMA) == {"category": "other"}
    assert len(server.requests) == 2 and "invalid" in server.requests[1]["body"]["messages"][-1]["content"]


def test_exhausted_retries_raise_schema_error(server):
    server.script = [(200, chat("nope"))] * 3
    model = OpenAIModel({"default": {"base_url": server.url, "model": "m", "schema_retries": 2}}, environ={})
    with pytest.raises(SchemaError):
        model.complete("s", "u", SCHEMA)
    assert len(server.requests) == 3


def test_connection_refused_falls_back_to_second_server(server, dead_url):
    """US2.2: primary endpoint down (connection refused) -> fallback profile answers."""
    models = {
        "default": {"base_url": dead_url, "model": "primary", "fallback": "backup"},
        "backup": {"base_url": server.url, "model": "backup-model"},
    }
    model = OpenAIModel(models, environ={})
    assert model.complete("s", "u", SCHEMA) == {"category": "tool"}
    assert model.last.used_fallback and server.requests[0]["body"]["model"] == "backup-model"


def test_primary_500_falls_back_but_context_overflow_does_not(server):
    other = FakeOpenAIServer()
    try:
        models = {
            "default": {"base_url": other.url, "model": "p", "fallback": "backup"},
            "backup": {"base_url": server.url, "model": "b"},
        }
        other.script = [(500, "boom")]
        assert OpenAIModel(models, environ={}).complete("s", "u", SCHEMA) == {"category": "tool"}
        other.script = [(400, '{"error": "maximum context length exceeded"}')]
        before = len(server.requests)
        with pytest.raises(ContextOverflow):
            OpenAIModel(models, environ={}).complete("s", "u", SCHEMA)
        assert len(server.requests) == before
    finally:
        other.close()


def test_all_down(dead_url):
    with pytest.raises(ModelUnavailable):
        OpenAIModel({"default": {"base_url": dead_url, "model": "m"}}, environ={}).complete("s", "u")


def test_timeout_is_unavailable():
    """FR-003 timeout_s: a server that never answers within the profile timeout is ModelUnavailable."""
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)  # accepts the TCP connection (backlog) but never replies
    try:
        url = f"http://127.0.0.1:{sock.getsockname()[1]}/v1"
        model = OpenAIModel({"default": {"base_url": url, "model": "m", "timeout_s": 1}}, environ={})
        with pytest.raises(ModelUnavailable, match="Timeout"):
            model.complete("s", "u")
    finally:
        sock.close()


def test_switching_server_is_a_models_json_change_only(server, tmp_path, write_json):
    """SC-002: LM Studio -> Ollama style endpoints differ only in models.json (URL, model)."""
    ollama_like = FakeOpenAIServer(prefix="/v1")
    try:
        from pkpipe.engine.config_models import ModelsConfig

        results = []
        for name, srv, model_name in (
            ("lmstudio", server, "qwen3.5-9b-mlx"),
            ("ollama", ollama_like, "qwen3:8b"),
        ):
            path = write_json(
                tmp_path / name, "models.json", {"default": {"base_url": srv.url, "model": model_name}}
            )
            cfg = ModelsConfig.model_validate(json.loads(path.read_text()))
            results.append(OpenAIModel(cfg, environ={}).complete("s", "u", SCHEMA))  # same code, other config
        assert results[0] == results[1] == {"category": "tool"}
        assert server.requests[-1]["body"]["model"] == "qwen3.5-9b-mlx"
        assert ollama_like.requests[-1]["body"]["model"] == "qwen3:8b"
    finally:
        ollama_like.close()
