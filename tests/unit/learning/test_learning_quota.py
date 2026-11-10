"""Spec 032: classified NotebookLM failures, bounded retry, the quota pre-check and the quota wait."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from pkpipe.learning.backends import NotebookLMBrowserBackend
from pkpipe.learning.errors import AuthExpired, BackendUnavailable, QuotaExceeded
from pkpipe.learning.failures import classify_failure, quota_reset_guess, quota_wait, until_text
from pkpipe.learning.gateway import GatewayConfig, LearningGateway
from pkpipe.learning.models import JobStatus
from pkpipe.learning.store import JobStore
from tests.support.notebooklm_driver import Driver


class ScriptedDriver(Driver):
    """Raises the queued errors of a step first; ``usage_data`` is the JSON of ``nlm usage --json``."""

    def __init__(self, errors=None, usage_data=None):
        super().__init__()
        self.errors = {k: list(v) for k, v in (errors or {}).items()}
        self.usage_data = usage_data

    def _do(self, name, *args):
        self.calls.append((name, *args))
        if self.errors.get(name):
            raise self.errors[name].pop(0)

    def usage(self):
        return self.usage_data


def usage(percent_left: float, resets_in_h: float = 3.0) -> dict:
    at = (datetime.now(UTC) + timedelta(hours=resets_in_h)).isoformat()
    return {"windows": [{"window": "rolling", "percent_remaining": percent_left, "resets_at": at}]}


def backend(driver, **kw):
    kw.setdefault("sleep", lambda s: None)
    return NotebookLMBrowserBackend(driver, **kw)


@pytest.mark.parametrize(
    "text, kind",
    [
        ("Audio generation limit reached for today", "quota"),
        ("HTTP 429 Too Many Requests", "quota"),
        ("You have exceeded the daily limit", "quota"),
        ("Not logged in, run nlm login", "auth"),
        ("session expired", "auth"),
        ("selector 'x' not found", "page_changed"),
        ("connection reset by peer", "transient"),
        ("something odd", "unknown"),
        ("", "unknown"),
    ],
)
def test_the_failure_text_of_a_client_is_classified(text, kind):
    assert classify_failure(text) == kind


def test_the_usage_windows_decide_when_the_quota_allows_new_work():
    assert quota_wait(usage(60)) is None
    blocked = quota_wait(usage(2, resets_in_h=2))
    assert blocked is not None and blocked > time.time()
    assert quota_wait({"windows": [{"percent_remaining": "x"}]}) is None  # unreadable never blocks
    assert quota_wait(None) is None
    two = {"windows": usage(1, 1)["windows"] + usage(0, 30)["windows"]}
    assert quota_wait(two) == quota_reset_guess({"windows": [two["windows"][1]]})  # the later reset wins
    assert until_text(None) == "an unknown time" and until_text(0) == "1970-01-01 00:00 UTC"


def test_an_exhausted_quota_is_found_before_anything_is_created():
    drv = ScriptedDriver(usage_data=usage(1, resets_in_h=2))
    with pytest.raises(QuotaExceeded) as err:
        backend(drv).create("https://example.invalid/x", "t", "i1")
    assert drv.calls == [] and err.value.retry_after and "waiting until" in str(err.value)


def test_a_driver_without_usage_or_a_failing_usage_never_blocks():
    drv = Driver()  # no usage()
    assert backend(drv).create("https://example.invalid/x", "t", "i1").nb_id == "nlm1"
    bad = ScriptedDriver(errors={})
    bad.usage = lambda: (_ for _ in ()).throw(RuntimeError("nlm failed"))
    assert backend(bad).create("https://example.invalid/x", "t", "i1").nb_id == "nlm1"


def test_a_transient_error_is_retried_with_a_backoff_and_then_succeeds():
    drv = ScriptedDriver(errors={"add_url_source": [ConnectionResetError("connection reset by peer")] * 2})
    slept: list[float] = []
    handle = backend(drv, sleep=slept.append, backoff_s=(1.0, 2.0)).create(
        "https://example.invalid/x", "t", "i"
    )
    assert handle.nb_id == "nlm1" and slept == [1.0, 2.0]
    assert [c[0] for c in drv.calls].count("add_url_source") == 3


def test_a_transient_error_is_retried_only_a_bounded_number_of_times():
    drv = ScriptedDriver(errors={"add_url_source": [ConnectionResetError("connection reset")] * 9})
    with pytest.raises(BackendUnavailable, match="transient error"):
        backend(drv, retries=1).create("https://example.invalid/x", "t", "i")
    assert [c[0] for c in drv.calls].count("add_url_source") == 2


def test_a_changed_page_and_a_login_are_never_retried():
    drv = ScriptedDriver(errors={"add_url_source": [RuntimeError("selector not found")]})
    with pytest.raises(BackendUnavailable, match="page may have changed"):
        backend(drv).create("https://example.invalid/x", "t", "i")
    assert [c[0] for c in drv.calls].count("add_url_source") == 1
    drv = ScriptedDriver(errors={"create_notebook": [RuntimeError("Not logged in, run nlm login")]})
    with pytest.raises(AuthExpired, match="nlm login"):
        backend(drv).create("https://example.invalid/x", "t", "i")


def test_the_audio_start_is_not_retried_and_a_quota_refusal_keeps_the_notebook():
    drv = ScriptedDriver(
        errors={"start_audio_overview": [RuntimeError("audio generation limit reached")]},
        usage_data=usage(50),
    )
    with pytest.raises(QuotaExceeded) as err:
        backend(drv).create("https://example.invalid/x", "t", "i")
    assert err.value.nb_id == "nlm1" and "quota" in str(err.value)
    assert [c[0] for c in drv.calls].count("start_audio_overview") == 1


def test_the_driver_message_is_part_of_the_reason_only_when_enabled():
    err = RuntimeError("weird failure in the studio panel")
    for detail, expected in ((False, False), (True, True)):
        drv = ScriptedDriver(errors={"create_notebook": [err]})
        with pytest.raises(BackendUnavailable) as caught:
            backend(drv, reason_detail=detail).create("https://example.invalid/x", "t", "i")
        assert ("weird failure" in str(caught.value)) is expected


def gateway(tmp_path, drv, clock):
    return LearningGateway(
        backend(drv),
        JobStore(tmp_path / "jobs.db"),
        GatewayConfig(audio_dir=tmp_path / "audio", block_private_networks=False),
        clock=clock,
        resolver=lambda host: ["93.184.216.34"],
    )


def test_a_quota_refusal_parks_the_notebook_and_the_next_run_starts_only_the_audio(tmp_path):
    now = [1_000_000.0]
    wake = now[0] + 3600
    drv = ScriptedDriver(errors={"start_audio_overview": [RuntimeError("audio generation limit reached")]})
    gw = gateway(tmp_path, drv, lambda: now[0])
    drv.usage_data = {
        "windows": [{"percent_remaining": 0, "resets_at": datetime.fromtimestamp(wake, UTC).isoformat()}]
    }
    # the gateway clock lies before the real one, so the pre-check passes and the audio start is refused
    with pytest.raises(QuotaExceeded) as err:
        gw.create("https://example.com/talk", "tpl", "item1")
    parked = gw.store.active_for_item("item1")
    assert parked is not None and parked.status is JobStatus.WAITING_QUOTA and parked.nb_id == "nlm1"
    assert err.value.nb_id == "nlm1"
    created_before = [c[0] for c in drv.calls].count("create_notebook")
    # still waiting: the same refusal, nothing is called
    drv.calls.clear()
    with pytest.raises(QuotaExceeded, match="waiting until"):
        gw.create("https://example.com/talk", "tpl", "item1")
    assert drv.calls == [] and created_before == 1
    # the wait is over: only the audio overview starts, on the notebook that already exists
    now[0] = wake + 1
    drv.usage_data = usage(90)
    out = gw.create("https://example.com/talk", "tpl", "item1")
    assert out["status"] == "generating" and out["nb_id"] == "nlm1"
    assert [c[0] for c in drv.calls] == ["start_audio_overview"]
    assert gw.store.get(parked.job_id).status is JobStatus.FAILED  # superseded, not left waiting
