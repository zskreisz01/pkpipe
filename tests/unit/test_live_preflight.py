"""How live tests adapt to the machine (tests/README.md, tests/support/preflight.py): the session preflight
sets the eval concurrency and model timeouts live tests use, blocks live tests on a critical system state
(fails them with PKPIPE_REQUIRE_LIVE=1), re-samples during the session and prints a summary. Inner pytest
runs with the real conftest and a fake probe."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pkpipe.adapters.model_openai import TIMEOUT_MULTIPLIER_ENV
from tests.fakes import FakeSystemProbe
from tests.fakes.system import healthy_snapshot
from tests.support import live, preflight

CONFTEST = (Path(__file__).resolve().parents[1] / "conftest.py").read_text(encoding="utf-8")


@pytest.fixture
def inner(pytester, monkeypatch):
    """An inner project whose live tests record what they were given; every service probe is up."""
    for name in live.PROBES:
        monkeypatch.setitem(live.PROBES, name, lambda: None)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.delenv("PKPIPE_REQUIRE_LIVE", raising=False)
    monkeypatch.delenv(TIMEOUT_MULTIPLIER_ENV, raising=False)
    monkeypatch.setenv("PKPIPE_MODEL_NAME", "model-under-test")
    monkeypatch.setenv("PKPIPE_JUDGE_MODEL", "judge-model")
    monkeypatch.setenv("PKPIPE_TEST_DATA_DIR", str(pytester.path / "data"))
    monkeypatch.setattr(preflight, "session", preflight.LiveSession())  # never touch the outer session
    probe = FakeSystemProbe()
    monkeypatch.setattr(preflight, "probe_factory", lambda: probe)
    pytester.makeconftest(CONFTEST)
    pytester.makeini("[pytest]\nmarkers =\n    live: needs a live service\n")
    pytester.makepyfile(
        """
        import os, pytest
        from tests.support import live

        SEEN = []

        @pytest.mark.live
        def test_model():
            SEEN.append((live.eval_concurrency(), os.environ.get("PKPIPE_MODEL_TIMEOUT_MULTIPLIER")))
            print("SEEN", SEEN[-1])

        @pytest.mark.live("lightpanda")
        def test_no_model():
            pass

        def test_plain():
            pass
        """
    )
    return probe


def _seen(result) -> list[str]:
    return [line.split("SEEN ", 1)[1].rstrip(".") for line in result.outlines if "SEEN (" in line]


def test_degraded_machine_lowers_concurrency_and_scales_timeouts(pytester, inner):
    inner.set(healthy_snapshot(memory={"pressure": "warn"}))
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider", "-s")
    result.assert_outcomes(passed=3)
    assert _seen(result) == ["(2, '1.5')"]
    out = "\n".join(result.outlines)
    assert "system preflight" in out and "preflight: degraded: concurrency 2, timeouts x1.5" in out
    assert "concurrency changes: none" in out
    assert os.environ.get(TIMEOUT_MULTIPLIER_ENV) is None  # restored at session end
    assert (pytester.path / "data" / "system-preflight.json").is_file()


def test_healthy_machine_keeps_four(pytester, inner):
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider", "-s")
    result.assert_outcomes(passed=3)
    assert _seen(result) == ["(4, '1')"]


def test_critical_system_state_skips_live_tests_or_fails_them_when_required(pytester, inner, monkeypatch):
    inner.set(healthy_snapshot(thermal={"level": "critical"}))
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider", "-s")
    result.assert_outcomes(passed=2, skipped=1)  # the lightpanda test needs no model: no preflight
    reasons = [r.longrepr[2] for r in result.reprec.getreports("pytest_runtest_logreport") if r.skipped]
    assert reasons == ["Skipped: system preflight blocks live tests: thermal state critical"]
    monkeypatch.setattr(preflight, "session", preflight.LiveSession())
    monkeypatch.setenv("PKPIPE_REQUIRE_LIVE", "1")
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2, errors=1)
    assert any("the system preflight blocks: thermal state critical" in line for line in result.outlines)


def test_llm_blocks_are_left_to_the_service_probes(pytester, inner):
    """A model the preflight sees as unloaded does not skip anything by itself: live.py's probes decide."""
    inner.set(healthy_snapshot(llm={"reachable": False, "models": ()}))
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider", "-s")
    result.assert_outcomes(passed=3)
    assert _seen(result) == ["(4, '2')"]  # blocked decision: ceiling kept, timeouts lengthened


def test_preflight_can_be_switched_off(pytester, inner, monkeypatch):
    monkeypatch.setenv("PKPIPE_LIVE_PREFLIGHT", "0")
    inner.set(healthy_snapshot(thermal={"level": "critical"}))
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider", "-s")
    result.assert_outcomes(passed=3)
    assert inner.calls == [] and _seen(result) == ["(4, None)"]


def test_mid_session_degradation_reaches_later_tests(pytester, inner, monkeypatch):
    """The sampler re-samples in the background: a later live test sees the lower concurrency."""
    monkeypatch.setenv("PKPIPE_LIVE_SAMPLE_S", "1")
    inner.set([healthy_snapshot(), healthy_snapshot(thermal={"level": "serious"})])
    pytester.makepyfile(
        test_two="""
        import os, time, pytest
        from tests.support import live

        @pytest.mark.live
        def test_a_first():
            print("SEEN", (live.eval_concurrency(), os.environ.get("PKPIPE_MODEL_TIMEOUT_MULTIPLIER")))
            time.sleep(2.5)

        @pytest.mark.live
        def test_b_later():
            print("SEEN", (live.eval_concurrency(), os.environ.get("PKPIPE_MODEL_TIMEOUT_MULTIPLIER")))
        """
    )
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider", "-s", "test_two.py")
    result.assert_outcomes(passed=2)
    assert _seen(result) == ["(4, '1')", "(1, '2')"]
    out = "\n".join(result.outlines)
    assert "last decision: degraded: concurrency 1" in out and "thermal events: +" in out
    assert "4 -> 1" in out
