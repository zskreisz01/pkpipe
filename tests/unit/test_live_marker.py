"""The `live` marker: skip when the service is missing, fail when PKPIPE_REQUIRE_LIVE=1 (tests/README.md)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.support import live

CONFTEST = (Path(__file__).resolve().parents[1] / "conftest.py").read_text(encoding="utf-8")
UNREACHABLE = "http://127.0.0.1:9/v1"  # discard port, nothing listens


@pytest.fixture(autouse=True)
def _unreachable(monkeypatch):
    monkeypatch.setenv("PKPIPE_MODEL_BASE_URL", UNREACHABLE)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)  # the inner run is a plain (serial) run
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


def test_live_tests_never_run_in_a_parallel_xdist_worker(pytester, monkeypatch):
    """Test policy (tests/README.md): live tests share one LM Studio and run serially; in an xdist worker they
    are skipped, and with PKPIPE_REQUIRE_LIVE=1 they fail, even when every service is up."""
    for name in live.PROBES:
        monkeypatch.setitem(live.PROBES, name, lambda: None)
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")
    monkeypatch.delenv("PKPIPE_REQUIRE_LIVE", raising=False)
    _project(pytester)
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, skipped=2)
    reasons = [r.longrepr[2] for r in result.reprec.getreports("pytest_runtest_logreport") if r.skipped]
    assert all("live tests run serially" in r for r in reasons)
    monkeypatch.setenv("PKPIPE_REQUIRE_LIVE", "1")
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, errors=2)
    assert any("live tests run serially" in line for line in result.outlines)


def test_each_xdist_worker_gets_its_own_data_dir(pytester, monkeypatch, tmp_path):
    """No shared app DB between parallel workers: a worker nests its own PKPIPE_DATA_DIR in the run's dir."""
    monkeypatch.setenv("PKPIPE_DATA_DIR", os.environ.get("PKPIPE_DATA_DIR", ""))  # restored afterwards
    monkeypatch.setenv("PKPIPE_TEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PKPIPE_TEST_DATA_WORKER", "")
    monkeypatch.delenv("PKPIPE_TEST_DATA_WORKER")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(
        """
        import os
        from pathlib import Path

        def test_dir():
            d = Path(os.environ["PKPIPE_DATA_DIR"])
            assert d.name.startswith("gw1-") and d.parent == Path(os.environ["OUTER"]) and d.is_dir()
        """
    )
    monkeypatch.setenv("OUTER", str(tmp_path))
    pytester.runpytest_inprocess("-p", "no:cacheprovider").assert_outcomes(passed=1)


def test_unknown_live_service_is_a_programming_error():
    with pytest.raises(KeyError, match="unknown live service"):
        live.unavailable_reason("quantum-computer")


def test_unreachable_server_reports_reason():
    assert live.lmstudio_models() == ()
    assert "not reachable" in live.unavailable_reason("lmstudio")
