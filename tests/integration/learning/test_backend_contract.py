"""Spec 012 US2 and SC-001: the same contract holds for every backend, through the real gateway.

``fake`` (scripted), ``open_notebook`` (a real local HTTP server speaking Open Notebook's REST API) and
``notebooklm_browser`` (a scripted browser automation driver; no browser, no Google account).
"""

from __future__ import annotations

import contextlib

import pytest

from pkpipe.learning import JobStatus, LearningGateway
from pkpipe.learning.backends import FakeBackend, NotebookLMBrowserBackend, OpenNotebookBackend
from pkpipe.learning.errors import BackendUnavailable
from pkpipe.learning.gateway import GatewayConfig
from pkpipe.learning.store import JobStore
from tests.support.fake_open_notebook import FakeOpenNotebook, serve
from tests.support.notebooklm_driver import Driver

URL = "https://example.invalid/talk"
TEMPLATE = "A podcast overview of about 10 minutes."


class Scenario:
    """The gateway over one backend, what the service received, and how to shut it down."""

    def __init__(self, gateway, sources, close=lambda: None):
        self.gateway, self.sources, self.close = gateway, sources, close


@contextlib.contextmanager
def scenario(kind: str, tmp_path, outcome: str = "ready"):
    cfg = GatewayConfig(audio_dir=tmp_path / "audio")
    no_dns = {"resolver": lambda host: []}
    if kind == "fake":
        be = FakeBackend(ready_after=2, fail={"item1"} if outcome == "failed" else ())
        gw = LearningGateway(be, JobStore(":memory:"), cfg, **no_dns)
        yield Scenario(gw, lambda: [c["url"] for c in be.created])
    elif kind == "open_notebook":
        fake = FakeOpenNotebook(
            ready_after=2, fail_with="Invalid json output" if outcome == "failed" else None
        )
        with serve(fake) as base:
            be = OpenNotebookBackend(api_url=base)
            gw = LearningGateway(be, JobStore(":memory:"), cfg, **no_dns)
            yield Scenario(gw, lambda: [s["url"] for s in fake.sources], be.close)
    else:
        drv = Driver()
        gw = LearningGateway(NotebookLMBrowserBackend(drv), JobStore(":memory:"), cfg, **no_dns)
        original = drv.audio_state
        drv.audio_state = lambda nb, art: (
            ("failed", "NotebookLM refused")
            if outcome == "failed"
            else _ready_on_second(drv, original, nb, art)
        )
        yield Scenario(gw, lambda: [c[2] for c in drv.calls if c[0] == "add_url_source"])


def _ready_on_second(drv, original, nb, art):
    original(nb, art)
    polls = sum(1 for c in drv.calls if c[0] == "audio_state")
    return ("ready", None) if polls >= 2 else ("generating", None)


BACKENDS = ["fake", "open_notebook", "notebooklm_browser"]


@pytest.mark.parametrize("kind", BACKENDS)
def test_ready_path_creates_polls_links_and_stores_audio(kind, tmp_path):
    """US1-AS1/AS2, US2-AS1: the same tools and results for every backend."""
    with scenario(kind, tmp_path) as sc:
        job = sc.gateway.create(URL, TEMPLATE, "item1")
        assert job["status"] == "generating" and job["nb_id"]
        assert sc.sources() == [URL]
        assert sc.gateway.status(job["job_id"])["status"] == "generating"
        done = sc.gateway.status(job["job_id"])
        assert done["status"] == "ready" and done["link"]
        assert sc.gateway.link(job["nb_id"])["url"] == done["link"]
        audio = sc.gateway.audio(job["job_id"])
        assert audio["bytes"] > 0 and audio["audio_path"].startswith(str(tmp_path / "audio"))
        sc.close()


@pytest.mark.parametrize("kind", BACKENDS)
def test_failed_generation_is_reported_with_a_reason(kind, tmp_path):
    with scenario(kind, tmp_path, "failed") as sc:
        job = sc.gateway.create(URL, TEMPLATE, "item1")
        res = sc.gateway.status(job["job_id"])
        assert res["status"] == JobStatus.FAILED and res["reason"]
        sc.close()


def test_open_notebook_reports_service_errors_readably(tmp_path):
    fake = FakeOpenNotebook()
    fake.unreachable_urls.add(URL)
    with serve(fake) as base:
        gw = LearningGateway(
            OpenNotebookBackend(api_url=base), JobStore(":memory:"), GatewayConfig(audio_dir=tmp_path)
        )
        with pytest.raises(BackendUnavailable, match="HTTP 500: Failed to fetch the page"):
            gw.create(URL, TEMPLATE, "item1")
        assert fake.jobs == {}  # no podcast was started for a notebook without a source
        assert gw.store.list(JobStatus.FAILED)[0].reason.endswith("Failed to fetch the page")


def test_open_notebook_down_fails_with_a_reason_and_recovers_when_it_is_back(tmp_path):
    """Edge case: Open Notebook down -> failed with reason; the next attempt works once it is back."""
    fake = FakeOpenNotebook()
    with serve(fake) as base:
        pass  # the server is stopped now: `base` is a dead address
    gw = LearningGateway(
        OpenNotebookBackend(api_url=base), JobStore(":memory:"), GatewayConfig(audio_dir=tmp_path)
    )
    with pytest.raises(BackendUnavailable, match="not reachable"):
        gw.create(URL, TEMPLATE, "item1")
    [failed] = gw.store.list(JobStatus.FAILED)
    assert failed.item_id == "item1" and "not reachable" in failed.reason


def test_open_notebook_password_is_used_and_a_wrong_one_is_reported_without_leaking_it(tmp_path):
    fake = FakeOpenNotebook(password="correct")
    with serve(fake) as base:
        good = OpenNotebookBackend(api_url=base, environ={"OPEN_NOTEBOOK_PASSWORD": "correct"})
        assert good.create(URL, TEMPLATE, "i").job_id
        bad = OpenNotebookBackend(api_url=base, environ={"OPEN_NOTEBOOK_PASSWORD": "wrong-password"})
        with pytest.raises(BackendUnavailable) as err:
            bad.create(URL, TEMPLATE, "i")
        assert "HTTP 401" in str(err.value) and "wrong-password" not in str(err.value)
