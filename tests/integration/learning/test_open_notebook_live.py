"""Spec 012 T003, T005, US1, SC-001: the gateway against a real Open Notebook with local Kokoro TTS.

Needs Docker, the stack of ``deploy/open-notebook`` (or ``~/tools/open-notebook``) and LM Studio. When the
stack is down the tests start it and stop it again afterwards; when it cannot be started they are skipped
with the reason (``PKPIPE_REQUIRE_LIVE=1`` turns the skip into a failure).

The source is a synthetic page served from a loopback HTTP server; Open Notebook (inside Docker) fetches it
through ``host.docker.internal``, so the gateway's private-network rule is switched off for these tests only.
Generation of the short podcast takes minutes on CPU: it is generated once and shared by the tests.
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import json
import threading
import time
import urllib.request
from pathlib import Path

import httpx
import pytest

from pkpipe.learning import JobStatus, LearningGateway
from pkpipe.learning.backends import OpenNotebookBackend
from pkpipe.learning.errors import BackendUnavailable
from pkpipe.learning.gateway import GatewayConfig
from pkpipe.learning.open_notebook_setup import SetupOptions
from pkpipe.learning.store import JobStore
from tests.support import live
from tests.support.open_notebook_stack import Stack
from tests.support.vault_mcp import free_port

pytestmark = pytest.mark.live("lmstudio", "open_notebook")

PAGE = """<html><head><title>Photosynthesis for beginners</title></head><body>
<h1>Photosynthesis for beginners</h1>
<p>Plants turn light, water and carbon dioxide into glucose and oxygen. Chlorophyll in the chloroplasts
absorbs the light. The light reactions happen in the thylakoid membranes, the Calvin cycle in the stroma.</p>
</body></html>"""
TEMPLATE = (
    "Make it a very short overview of about one minute in English: three segments, "
    "each with only two short turns of speech."
)
GENERATION_DEADLINE_S = 30 * 60


@pytest.fixture(scope="module")
def stack():
    s = Stack()
    s.ensure_up()
    models = live.lmstudio_models()
    if SetupOptions().podcast_model not in models:
        pytest.skip(f"podcast model '{SetupOptions().podcast_model}' is not loaded in LM Studio")
    s.configure()
    yield s
    s.stop()


@pytest.fixture(scope="module")
def page_url():
    """A page on a loopback-only server, reachable from the container as host.docker.internal."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="pkpipe-live-page-") as folder:
        (Path(folder) / "photosynthesis.html").write_text(PAGE, encoding="utf-8")
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=folder)
        port = free_port()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            yield f"http://host.docker.internal:{port}/photosynthesis.html"
        finally:
            server.shutdown()
            server.server_close()


def make_gateway(stack, tmp_path_factory, name="gw", timeout_s=GENERATION_DEADLINE_S):
    base = tmp_path_factory.mktemp(name)
    backend = OpenNotebookBackend(api_url=stack.api_url)
    config = GatewayConfig(timeout_s=timeout_s, block_private_networks=False, audio_dir=base / "audio")
    return LearningGateway(backend, JobStore(base / "jobs.db"), config)


@pytest.fixture(scope="module")
def generated(stack, page_url, tmp_path_factory):
    """One notebook with a short podcast, created through the gateway; the job is polled by the tests."""
    gw = make_gateway(stack, tmp_path_factory, "live-gw")
    job = gw.create(page_url, TEMPLATE, "live-item-1")
    yield gw, job
    with contextlib.suppress(Exception):  # leave the stack as it was: no test notebook, no test episode
        for episode in httpx.get(f"{stack.api_url}/api/podcasts/episodes", timeout=30).json():
            if episode["name"] == f"pkpipe {job['item_id']}":
                httpx.delete(f"{stack.api_url}/api/podcasts/episodes/{episode['id']}", timeout=30)
        httpx.delete(
            f"{stack.api_url}/api/notebooks/{job['nb_id']}?delete_exclusive_sources=true", timeout=30
        )


def api(stack, path):
    return httpx.get(f"{stack.api_url}{path}", timeout=30).json()


def test_notebook_is_created_from_the_source_and_a_podcast_job_starts(stack, generated):
    """US1-AS1: a notebook from the URL, the page content is in the source, a job is running."""
    gw, job = generated
    assert job["status"] == "generating" and job["nb_id"].startswith("notebook:")
    notebook = api(stack, f"/api/notebooks/{job['nb_id']}")
    assert notebook["source_count"] == 1
    sources = api(stack, f"/api/sources?notebook_id={job['nb_id']}")
    assert len(sources) == 1 and sources[0]["asset"]["url"].endswith("/photosynthesis.html")
    detail = api(stack, f"/api/sources/{sources[0]['id']}")
    assert "thylakoid" in (detail["full_text"] or "")  # Open Notebook fetched the synthetic page
    state = api(stack, f"/api/podcasts/jobs/{job['job_id']}")
    assert state["status"] in ("submitted", "running", "completed")


def test_link_opens_the_notebook_in_the_open_notebook_ui(stack, generated):
    gw, job = generated
    url = gw.link(job["nb_id"])["url"]
    assert url == f"http://127.0.0.1:8502/notebooks/{job['nb_id'].replace(':', '%3A')}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        assert resp.status == 200


def test_short_podcast_is_generated_with_kokoro_and_downloaded(stack, generated):
    """US1-AS2, SC-001 (ready): poll until ready, then audio and link are stored."""
    gw, job = generated
    deadline = time.time() + GENERATION_DEADLINE_S
    status = gw.status(job["job_id"])
    while status["status"] == "generating" and time.time() < deadline:
        time.sleep(15)
        status = gw.status(job["job_id"])
    assert status["status"] == JobStatus.READY, status
    assert status["link"].startswith("http://127.0.0.1:8502/notebooks/")
    audio = gw.audio(job["job_id"])
    data = Path(audio["audio_path"]).read_bytes()
    assert len(data) > 20_000, "a spoken overview is more than a few KB"
    assert data[:3] == b"ID3" or data[0] == 0xFF, "an MP3 file"
    print(f"generated in {status['elapsed_s']} s, {len(data)} bytes")


def test_a_page_open_notebook_cannot_fetch_fails_with_a_reason_and_starts_no_podcast(stack, tmp_path_factory):
    """Edge case: the service reports the failure; the gateway records it and no generation is started."""
    gw = make_gateway(stack, tmp_path_factory, "live-bad")
    before = len(api(stack, "/api/podcasts/episodes"))
    with pytest.raises(BackendUnavailable) as err:
        gw.create("http://host.docker.internal:1/nothing-listens-here", TEMPLATE, "live-item-bad")
    assert "Open Notebook" in str(err.value)
    [failed] = gw.store.list(JobStatus.FAILED)
    assert failed.item_id == "live-item-bad" and failed.reason
    assert len(api(stack, "/api/podcasts/episodes")) == before


def test_blocked_url_never_reaches_open_notebook(stack, tmp_path_factory):
    """Constitution IV with the default filter on: a private address creates no notebook at all."""
    base = tmp_path_factory.mktemp("live-blocked")
    gw = LearningGateway(
        OpenNotebookBackend(api_url=stack.api_url),
        JobStore(base / "jobs.db"),
        GatewayConfig(audio_dir=base / "audio"),
    )
    names_before = {n["name"] for n in api(stack, "/api/notebooks")}
    with pytest.raises(Exception, match="private_network"):
        gw.create("http://127.0.0.1:8765/secret", TEMPLATE, "live-item-blocked")
    assert {n["name"] for n in api(stack, "/api/notebooks")} == names_before


def test_setup_script_is_idempotent_against_the_real_stack(stack):
    """T005: a second run of the setup changes nothing that matters (no duplicate models or profiles)."""

    def counts():
        return {
            k: len(api(stack, f"/api/{k}"))
            for k in ("models", "credentials", "episode-profiles", "speaker-profiles")
        }

    before = counts()
    stack.configure()
    assert counts() == before
    profile = next(p for p in api(stack, "/api/episode-profiles") if p["name"] == "pkpipe_overview")
    assert profile["language"] == "en" and profile["num_segments"] >= 3
    json.dumps(profile)  # serialisable, no surprises
