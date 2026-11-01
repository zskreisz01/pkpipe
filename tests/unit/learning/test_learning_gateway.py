"""Spec 012 FR-001, FR-002, FR-003, SC-001: the gateway service over the fake backend."""

from __future__ import annotations

import pytest

from pkpipe.learning import (
    BackendUnavailable,
    JobStatus,
    LearningGateway,
    UrlBlocked,
)
from pkpipe.learning.errors import LearningError, NotReady, UnknownJob
from pkpipe.learning.gateway import GatewayConfig, clean_reason
from pkpipe.learning.store import JobStore
from tests.fakes import FakeLearningBackend

URL = "https://example.invalid/talk"
TEMPLATE = "A podcast overview of about 10 minutes."


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


def make(tmp_path, clock, backend=None, **cfg):
    backend = backend or FakeLearningBackend(ready_after=2)
    config = GatewayConfig(audio_dir=tmp_path / "audio", **cfg)
    gw = LearningGateway(backend, JobStore(":memory:"), config, clock=clock, resolver=lambda h: [])
    return gw, backend


def test_create_returns_a_generating_job_and_forwards_only_url_template_and_item(tmp_path, clock):
    """US1-AS1, FR-001, FR-002: the backend receives the URL, the template and the item id, nothing else."""
    gw, backend = make(tmp_path, clock)
    job = gw.create(URL, TEMPLATE, "item1")
    assert job["status"] == "generating" and job["job_id"] and job["nb_id"] and job["backend"] == "fake"
    assert backend.created == [{"url": URL, "template": TEMPLATE, "item_id": "item1"}]
    assert "backend_ref" not in job  # backend internals stay inside the gateway


def test_status_polls_until_ready_then_link_and_audio_are_available(tmp_path, clock):
    """US1-AS2: ready after polling; audio stored; link returned."""
    gw, backend = make(tmp_path, clock)
    job = gw.create(URL, TEMPLATE, "item1")
    assert gw.status(job["job_id"])["status"] == "generating"
    done = gw.status(job["job_id"])
    assert done["status"] == "ready" and done["link"].endswith(job["nb_id"])
    assert gw.link(job["nb_id"]) == {"nb_id": job["nb_id"], "url": done["link"]}
    audio = gw.audio(job["job_id"])
    assert audio["bytes"] == len(backend.audio_bytes) and audio["audio_path"].endswith(".mp3")
    assert (
        gw.audio(job["job_id"])["audio_path"] == audio["audio_path"]
    )  # second call: stored file, no download
    assert gw.status(job["job_id"])["audio_path"] == audio["audio_path"]
    assert backend.polls[job["job_id"]] == 2  # a finished job is not polled again


def test_generation_failure_is_recorded_with_its_reason(tmp_path, clock):
    """SC-001 (failed), FR-003: failure reasons are recorded."""
    gw, _ = make(tmp_path, clock, FakeLearningBackend(fail={"bad"}, fail_reason="TTS crashed\nstack..."))
    job = gw.create(URL, TEMPLATE, "bad")
    res = gw.status(job["job_id"])
    assert res["status"] == "failed" and res["reason"] == "TTS crashed stack..."
    with pytest.raises(NotReady, match="failed"):
        gw.audio(job["job_id"])


def test_timeout_fails_the_job_with_a_reason_and_is_configurable(tmp_path, clock):
    """SC-001 (timeout), FR-003: default 90 minutes, configurable."""
    gw, _ = make(tmp_path, clock, FakeLearningBackend(never={"slow"}))
    assert gw.config.timeout_s == 90 * 60
    job = gw.create(URL, TEMPLATE, "slow")
    clock.t += 90 * 60 - 1
    assert gw.status(job["job_id"])["status"] == "generating"
    clock.t += 1
    res = gw.status(job["job_id"])
    assert res["status"] == "failed" and res["reason"] == "timeout after 90 min"

    gw2, _ = make(tmp_path, clock, FakeLearningBackend(never={"slow"}), timeout_s=600)
    job2 = gw2.create(URL, TEMPLATE, "slow")
    clock.t += 600
    assert gw2.status(job2["job_id"])["reason"] == "timeout after 10 min"


def test_backend_down_on_create_raises_and_records_a_failed_job(tmp_path, clock):
    """Edge case: Open Notebook down -> failed with a reason (the flow retries next day)."""
    gw, _ = make(tmp_path, clock, FakeLearningBackend(down=True))
    with pytest.raises(BackendUnavailable, match="down"):
        gw.create(URL, TEMPLATE, "item1")
    [job] = gw.store.list(JobStatus.FAILED)
    assert job.item_id == "item1" and job.reason == "fake backend is down"


def test_an_active_item_is_reused_and_a_failed_one_is_created_again(tmp_path, clock):
    """No duplicate notebooks after a restart or a repeated run; failed jobs are retried."""
    gw, backend = make(tmp_path, clock, FakeLearningBackend(fail={"item1"}))
    first = gw.create(URL, TEMPLATE, "item1")
    assert gw.create(URL, TEMPLATE, "item1")["job_id"] == first["job_id"]
    assert len(backend.created) == 1  # the active job was reused, no second notebook
    assert gw.status(first["job_id"])["status"] == "failed"
    gw.create(URL, TEMPLATE, "item1")
    assert len(backend.created) == 2  # a failed job does not block the retry


def test_blocked_urls_never_reach_the_backend(tmp_path, clock):
    """Constitution IV: the URL filter runs before anything is forwarded."""
    gw, backend = make(tmp_path, clock, deny=["evil.example"])
    for url in ("http://127.0.0.1/admin", "file:///etc/passwd", "https://x.evil.example/p"):
        with pytest.raises(UrlBlocked):
            gw.create(url, TEMPLATE, "item1")
    assert backend.created == []


def test_private_networks_pass_when_the_filter_is_configured_off(tmp_path, clock):
    gw, backend = make(tmp_path, clock, block_private_networks=False)
    gw.create("http://127.0.0.1:8765/page", TEMPLATE, "item1")
    assert backend.created[0]["url"] == "http://127.0.0.1:8765/page"


def test_oversized_template_is_refused(tmp_path, clock):
    gw, backend = make(tmp_path, clock)
    with pytest.raises(LearningError, match="template too long"):
        gw.create(URL, "x" * 5000, "item1")
    assert backend.created == []


def test_unknown_job_and_notebook(tmp_path, clock):
    gw, _ = make(tmp_path, clock)
    with pytest.raises(UnknownJob):
        gw.status("nope")
    with pytest.raises(UnknownJob):
        gw.link("nope")
    with pytest.raises(UnknownJob):
        gw.audio("nope")


def test_audio_before_ready_is_refused(tmp_path, clock):
    gw, _ = make(tmp_path, clock)
    job = gw.create(URL, TEMPLATE, "item1")
    with pytest.raises(NotReady, match="generating"):
        gw.audio(job["job_id"])


def test_repeated_poll_errors_fail_the_job_but_a_single_one_does_not(tmp_path, clock):
    class Flaky(FakeLearningBackend):
        def __init__(self):
            super().__init__(ready_after=1)
            self.broken = True

        def poll(self, handle):
            if self.broken:
                raise BackendUnavailable("Open Notebook not reachable at http://127.0.0.1:5055")
            return super().poll(handle)

    backend = Flaky()
    gw, _ = make(tmp_path, clock, backend)
    job = gw.create(URL, TEMPLATE, "a")
    assert gw.status(job["job_id"])["status"] == "generating"  # one hiccup: still generating
    backend.broken = False
    assert gw.status(job["job_id"])["status"] == "ready"  # recovered; the counter reset
    backend.broken = True
    job2 = gw.create(URL, TEMPLATE, "b")
    states = [gw.status(job2["job_id"])["status"] for _ in range(5)]
    assert states[:4] == ["generating"] * 4 and states[4] == "failed"
    assert "backend unavailable" in gw.status(job2["job_id"])["reason"]


def test_failure_reasons_are_single_line_and_bounded():
    assert clean_reason("a\n\tb\x00c   d") == "a b c d"
    assert len(clean_reason("x" * 1000)) == 300
    assert clean_reason("  \n ") == "unknown error"


def test_job_store_survives_a_restart(tmp_path, clock):
    """A gateway restart in the middle of a generation keeps polling the same job."""
    backend = FakeLearningBackend(ready_after=3)
    path = tmp_path / "jobs.db"
    gw = LearningGateway(backend, JobStore(path), GatewayConfig(audio_dir=tmp_path), clock=clock)
    job = gw.create(URL, TEMPLATE, "item1")
    gw.status(job["job_id"])
    gw.store.close()
    gw2 = LearningGateway(backend, JobStore(path), GatewayConfig(audio_dir=tmp_path), clock=clock)
    assert gw2.status(job["job_id"])["status"] == "generating"
    assert gw2.status(job["job_id"])["status"] == "ready"
    assert gw2.create(URL, TEMPLATE, "item1")["job_id"] == job["job_id"]
