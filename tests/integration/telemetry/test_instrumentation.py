"""Spec 003 FR-002: OpenInference (LangGraph), httpx and FastAPI instrumentation produce spans."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TypedDict

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.graph import END, START, StateGraph

from pkpipe.telemetry import ContentPolicy, flow_span, step_span

CANARY = "JOURNAL-CANARY-44d1"


class State(TypedDict):
    text: str
    out: str


def _graph(reply: str):
    model = FakeListChatModel(responses=[reply])

    def node(state: State) -> State:
        return {"text": state["text"], "out": model.invoke(state["text"]).content}

    g = StateGraph(State)
    g.add_node("summarize", node)
    g.add_edge(START, "summarize")
    g.add_edge("summarize", END)
    return g.compile()


@pytest.fixture
def instrumented(otel_factory):
    created = []

    def make(policy=None, **kw):
        o = otel_factory(policy)
        o.telemetry.instrument(**kw)
        created.append(o)
        return o

    yield make


def test_langgraph_run_is_traced_by_openinference_under_the_flow(instrumented):
    """FR-002, US1.1: graph, node and LLM spans share the flow's trace."""
    o = instrumented(httpx=False)
    with flow_span("learning", trigger="manual", config_version="v1"), step_span("run", "Task_Run"):
        result = _graph("a summary").invoke({"text": "hello", "out": ""})
    assert result["out"] == "a summary"
    names = {s.name for s in o.spans()}
    assert {"summarize", "FakeListChatModel"} <= names or any("Fake" in n for n in names), names
    flow = o.spans("flow ")[0]
    assert all(s.context.trace_id == flow.context.trace_id for s in o.spans())
    kinds = {s.attributes.get("openinference.span.kind") for s in o.spans()}
    assert "LLM" in kinds and "CHAIN" in kinds


def test_openinference_content_is_scrubbed_for_journal_flows_and_kept_for_opted_in_flows(instrumented):
    """FR-004, SC-002: same instrumented graph; capture decided by the flow, enforced at export."""
    o = instrumented(ContentPolicy(frozenset({"learning", "journal"})), httpx=False)
    with flow_span("learning", trigger="manual", config_version="v1"):
        _graph("public reply").invoke({"text": "public prompt", "out": ""})
    with flow_span("journal", trigger="manual", config_version="v1", data_classes=("journal",)):
        _graph(f"reply {CANARY}").invoke({"text": f"diary {CANARY}", "out": ""})
    spans = o.spans()
    learning_trace = o.spans("flow learning")[0].context.trace_id
    journal_trace = o.spans("flow journal")[0].context.trace_id
    learning = [s for s in spans if s.context.trace_id == learning_trace]
    journal = [s for s in spans if s.context.trace_id == journal_trace]
    assert any("public prompt" in repr(s.attributes) for s in learning), "opted-in flow keeps content"
    assert journal and CANARY not in repr([(s.attributes, s.events) for s in journal])
    # the exporter is the enforcement point: the raw Telemetry spans carry the decision
    assert all(s.attributes["pkpipe.content_capture"] is False for s in journal)


def test_spans_outside_any_flow_default_to_no_content(instrumented):
    o = instrumented(httpx=False)
    _graph(f"r {CANARY}").invoke({"text": CANARY, "out": ""})
    assert o.spans() and CANARY not in repr([s.attributes for s in o.spans()])


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def test_httpx_calls_become_client_spans_in_the_flow_trace(instrumented):
    """FR-002: HTTP calls are traced."""
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        o = instrumented(langchain=False)
        with flow_span("learning", trigger="manual", config_version="v1"), step_span("fetch", "Task_Fetch"):
            r = httpx.get(f"http://127.0.0.1:{srv.server_address[1]}/page")
        assert r.text == "ok"
    finally:
        srv.shutdown()
    http = [
        s for s in o.spans() if s.attributes.get("http.request.method") or s.attributes.get("http.method")
    ]
    assert http, [s.name for s in o.spans()]
    assert http[0].context.trace_id == o.spans("flow ")[0].context.trace_id
    assert http[0].parent.span_id == o.spans("step ")[0].context.span_id


def test_fastapi_requests_are_traced(otel_factory):
    """FR-002: FastAPI instrumentation."""
    o = otel_factory()
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"ok": True}

    o.telemetry.instrument(langchain=False, httpx=False, fastapi_app=app)
    assert TestClient(app).get("/health").json() == {"ok": True}
    server = [s for s in o.spans() if s.attributes.get("http.route") == "/health"]
    assert server and server[0].attributes["http.status_code"] == 200
