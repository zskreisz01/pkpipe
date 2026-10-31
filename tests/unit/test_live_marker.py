"""The `live` marker: skip when the service is missing, fail when PKPIPE_REQUIRE_LIVE=1 (tests/README.md)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import live

CONFTEST = (Path(__file__).resolve().parents[1] / "conftest.py").read_text(encoding="utf-8")
UNREACHABLE = "http://127.0.0.1:9/v1"  # discard port, nothing listens


@pytest.fixture(autouse=True)
def _unreachable(monkeypatch):
    monkeypatch.setenv("PKPIPE_MODEL_BASE_URL", UNREACHABLE)
    live.lmstudio_models.cache_clear()
    yield
    live.lmstudio_models.cache_clear()


def _project(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(CONFTEST)
    pytester.makeini("[pytest]\nmarkers =\n    live: needs a live service\n")
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.live
        def test_needs_model():
            assert False, "must not run when LM Studio is down"

        @pytest.mark.live("lightpanda")
        def test_needs_other_service():
            pass

        def test_plain():
            pass
        """
    )


def test_live_tests_are_skipped_when_service_unreachable(pytester, monkeypatch):
    monkeypatch.delenv("PKPIPE_REQUIRE_LIVE", raising=False)
    monkeypatch.setattr(live, "_lightpanda", lambda: "lightpanda not installed")
    monkeypatch.setitem(live.PROBES, "lightpanda", lambda: "lightpanda not installed")
    _project(pytester)
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, skipped=2)
    reasons = [r.longrepr[2] for r in result.reprec.getreports("pytest_runtest_logreport") if r.skipped]
    assert any("LM Studio not reachable" in r for r in reasons)
    assert any("lightpanda not installed" in r for r in reasons)


def test_require_live_turns_skip_into_failure(pytester, monkeypatch):
    monkeypatch.setenv("PKPIPE_REQUIRE_LIVE", "1")
    monkeypatch.setitem(live.PROBES, "lightpanda", lambda: None)
    _project(pytester)
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2, errors=1)  # live test errors in setup, others pass
    assert any("PKPIPE_REQUIRE_LIVE=1" in line for line in result.outlines)


def test_unknown_live_service_is_a_programming_error():
    with pytest.raises(KeyError, match="unknown live service"):
        live.unavailable_reason("quantum-computer")


def test_unreachable_server_reports_reason():
    assert live.lmstudio_models() == ()
    assert "not reachable" in live.unavailable_reason("lmstudio")
