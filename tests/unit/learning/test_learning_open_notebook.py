"""Spec 012 T003: the Open Notebook backend speaks the REST API correctly and fails readably."""

from __future__ import annotations

import json

import httpx
import pytest

from pkpipe.learning.backend import BackendHandle
from pkpipe.learning.backends import OpenNotebookBackend
from pkpipe.learning.errors import BackendUnavailable
from pkpipe.learning.models import JobStatus


class Server:
    """Records requests and answers from a route table: ``{(method, path): response | callable}``."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body, dict(request.headers)))
        route = self.routes.get((request.method, request.url.path))
        if route is None:
            return httpx.Response(404, json={"detail": "no such route"})
        return route(request) if callable(route) else route


def backend(routes, environ=None, **kw):
    server = Server(routes)
    return OpenNotebookBackend(transport=httpx.MockTransport(server), environ=environ or {}, **kw), server


CREATE_ROUTES = {
    ("POST", "/api/notebooks"): httpx.Response(200, json={"id": "notebook:abc"}),
    ("POST", "/api/sources/json"): httpx.Response(200, json={"id": "source:1"}),
    ("POST", "/api/podcasts/generate"): httpx.Response(
        200, json={"job_id": "command:j1", "status": "submitted"}
    ),
}


def test_create_makes_notebook_link_source_and_starts_the_podcast():
    be, server = backend(CREATE_ROUTES)
    handle = be.create("https://example.invalid/talk", "About ten minutes.", "item1")
    assert handle == BackendHandle(nb_id="notebook:abc", job_id="command:j1", ref=None)
    calls = {(m, p): body for m, p, body, _ in server.requests}
    assert [(m, p) for m, p, *_ in server.requests] == [
        ("POST", "/api/notebooks"),
        ("POST", "/api/sources/json"),
        ("POST", "/api/podcasts/generate"),
    ]
    source = calls[("POST", "/api/sources/json")]
    assert source["type"] == "link" and source["url"] == "https://example.invalid/talk"
    assert source["notebooks"] == ["notebook:abc"] and source["async_processing"] is False
    podcast = calls[("POST", "/api/podcasts/generate")]
    assert podcast["notebook_id"] == "notebook:abc" and podcast["briefing_suffix"] == "About ten minutes."
    assert podcast["episode_profile"] == "pkpipe_overview" and podcast["speaker_profile"] == "pkpipe_duo"
    assert calls[("POST", "/api/notebooks")]["name"] == "pkpipe item1"


def test_poll_maps_job_states():
    jobs = {
        "command:run": {"status": "running", "result": {}},
        "command:ok": {"status": "completed", "result": {"episode_id": "episode:e1"}},
        "command:noep": {"status": "completed", "result": {}},
        "command:bad": {"status": "failed", "error_message": "\nInvalid json output: cut off\nmore"},
        "command:weird": {"status": "cancelled", "error_message": ""},
    }
    routes = {("GET", f"/api/podcasts/jobs/{j}"): httpx.Response(200, json=v) for j, v in jobs.items()}
    be, _ = backend(routes)
    poll = lambda j: be.poll(BackendHandle("nb", j))  # noqa: E731
    assert poll("command:run").status is JobStatus.GENERATING
    ok = poll("command:ok")
    assert ok.status is JobStatus.READY and ok.ref == "episode:e1"
    assert poll("command:noep").status is JobStatus.FAILED
    bad = poll("command:bad")
    assert bad.status is JobStatus.FAILED and bad.reason == "Invalid json output: cut off"
    assert poll("command:weird").reason == "Open Notebook job cancelled"


def test_link_points_at_the_notebook_in_the_ui():
    be, _ = backend({}, ui_url="http://127.0.0.1:8502/")
    assert be.link("notebook:abc") == "http://127.0.0.1:8502/notebooks/notebook%3Aabc"


def test_audio_is_downloaded_atomically(tmp_path):
    routes = {("GET", "/api/podcasts/episodes/episode:e1/audio"): httpx.Response(200, content=b"ID3audio")}
    be, _ = backend(routes)
    path = be.fetch_audio(BackendHandle("nb", "j", ref="episode:e1"), tmp_path)
    assert path.read_bytes() == b"ID3audio" and path.suffix == ".mp3"
    assert [p.name for p in tmp_path.iterdir()] == [path.name]  # no .part file left


def test_audio_failures(tmp_path):
    be, _ = backend({("GET", "/api/podcasts/episodes/episode:e1/audio"): httpx.Response(200, content=b"")})
    with pytest.raises(BackendUnavailable, match="empty audio"):
        be.fetch_audio(BackendHandle("nb", "j", ref="episode:e1"), tmp_path)
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(BackendUnavailable, match="no episode id"):
        be.fetch_audio(BackendHandle("nb", "j"), tmp_path)
    be2, _ = backend({})  # 404
    with pytest.raises(BackendUnavailable, match="HTTP 404"):
        be2.fetch_audio(BackendHandle("nb", "j", ref="episode:e1"), tmp_path)


def test_service_down_is_a_readable_error_without_secrets():
    def refuse(request):
        raise httpx.ConnectError("[Errno 61] Connection refused", request=request)

    be = OpenNotebookBackend(
        api_url="http://127.0.0.1:5055",
        transport=httpx.MockTransport(refuse),
        environ={"OPEN_NOTEBOOK_PASSWORD": "s3cret"},
    )
    with pytest.raises(BackendUnavailable) as err:
        be.create("https://example.invalid/x", "", "item1")
    assert str(err.value) == "Open Notebook not reachable at http://127.0.0.1:5055 (ConnectError)"
    assert "s3cret" not in str(err.value)


def test_http_errors_carry_status_and_detail_only():
    routes = {("POST", "/api/notebooks"): httpx.Response(500, json={"detail": "database is locked\ntrace"})}
    be, _ = backend(routes)
    with pytest.raises(BackendUnavailable, match="HTTP 500: database is locked$"):
        be.create("https://example.invalid/x", "", "item1")


def test_a_source_that_cannot_be_fetched_stops_before_the_podcast():
    routes = {
        **CREATE_ROUTES,
        ("POST", "/api/sources/json"): httpx.Response(422, json={"detail": "fetch failed"}),
    }
    be, server = backend(routes)
    with pytest.raises(BackendUnavailable, match="HTTP 422"):
        be.create("https://example.invalid/x", "", "item1")
    assert ("POST", "/api/podcasts/generate") not in [(m, p) for m, p, *_ in server.requests]


def test_missing_ids_in_the_reply_are_errors():
    be, _ = backend({("POST", "/api/notebooks"): httpx.Response(200, json={"name": "x"})})
    with pytest.raises(BackendUnavailable, match="no notebook id"):
        be.create("https://example.invalid/x", "", "item1")


def test_password_is_sent_only_when_configured():
    routes = {**CREATE_ROUTES}
    be, server = backend(routes, environ={"OPEN_NOTEBOOK_PASSWORD": "pw"})
    be.create("https://example.invalid/x", "", "i")
    assert server.requests[0][3]["authorization"] == "Bearer pw"
    be2, server2 = backend(routes)
    be2.create("https://example.invalid/x", "", "i")
    assert "authorization" not in server2.requests[0][3]
