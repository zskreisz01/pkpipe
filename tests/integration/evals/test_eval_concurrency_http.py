"""Eval concurrency end to end (chore test-speed): the real OpenAI adapter against a real local
OpenAI-compatible server that answers slowly, like LM Studio with parallel predictions. ``--concurrency 4``
keeps four requests in flight and finishes several times faster, with the same report."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pkpipe import app
from pkpipe.engine.registry import load_effective
from pkpipe.evals.runner import EvalRunner
from tests.support.evals import mini_evals, triage_case

DELAY_S = 0.25
ROWS = [
    triage_case(f"c{i:02}", "zz-cli" if i % 2 else "zz-ad", "tool" if i % 2 else "marketing")
    for i in range(12)
]


class SlowServer:
    """``/v1/chat/completions`` that waits ``DELAY_S`` per request; records the peak of parallel requests."""

    def __init__(self):
        self.lock = threading.Lock()
        self.in_flight = self.peak = self.requests = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
                with outer.lock:
                    outer.in_flight += 1
                    outer.requests += 1
                    outer.peak = max(outer.peak, outer.in_flight)
                try:
                    time.sleep(DELAY_S)
                    user = body["messages"][-1]["content"]
                    category = "tool" if "zz-cli" in user else "marketing"
                    content = json.dumps({"category": category, "confidence": 0.9, "reason": "synthetic"})
                    data = json.dumps(
                        {
                            "choices": [{"message": {"role": "assistant", "content": content}}],
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                        }
                    ).encode()
                finally:
                    with outer.lock:
                        outer.in_flight -= 1
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(monkeypatch):
    s = SlowServer()
    monkeypatch.setenv("PKPIPE_MODEL_BASE_URL", s.url)
    monkeypatch.setenv("PKPIPE_MODEL_NAME", "slow-synthetic-model")
    monkeypatch.setenv("PKPIPE_JUDGE_MODEL", "slow-synthetic-judge")
    yield s
    s.close()


def _run(server: SlowServer, pack_dir, tmp_path, concurrency: int):
    snap = load_effective(pack_dir, None).snapshot()
    paths = mini_evals(tmp_path / f"n{concurrency}", {"datasets/triage.jsonl": ROWS})
    server.peak = server.requests = 0
    start = time.monotonic()
    report = (
        EvalRunner(snap, paths=paths, model=app.make_model(snap), concurrency=concurrency)
        .run(["triage"])
        .suite("triage")
    )
    return report, time.monotonic() - start, server.peak, server.requests


def test_concurrency_keeps_n_requests_in_flight_and_gives_the_same_report(server, pack_dir, tmp_path):
    """Serial: one request at a time, 12 x 0.25 s. Concurrency 4: four in flight, at least 2.5x faster, and
    the same cases in the same order with the same metrics."""
    serial, serial_s, serial_peak, serial_n = _run(server, pack_dir, tmp_path, 1)
    fast, fast_s, fast_peak, fast_n = _run(server, pack_dir, tmp_path, 4)
    assert serial_peak == 1 and fast_peak == 4
    assert serial_n == fast_n == len(ROWS)
    assert serial_s >= len(ROWS) * DELAY_S and fast_s * 2.5 <= serial_s, (serial_s, fast_s)
    assert [c.case_id for c in fast.cases] == [r["id"] for r in ROWS]
    assert [c.model_dump() for c in fast.cases] == [c.model_dump() for c in serial.cases]
    assert fast.metrics == serial.metrics and fast.metrics["accuracy"] == 1.0
