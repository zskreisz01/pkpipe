"""Spec 012 T006 / US2: the NotebookLM browser backend sits behind the same interface and fails fast."""

from __future__ import annotations

import pytest

from pkpipe.learning import LearningBackend
from pkpipe.learning.backend import BackendHandle
from pkpipe.learning.backends import NotebookLMBrowserBackend, build_backend
from pkpipe.learning.errors import BackendUnavailable, LearningError
from pkpipe.learning.models import BackendName, JobStatus
from tests.support.notebooklm_driver import Driver


def test_backend_satisfies_the_interface_and_runs_the_same_sequence(tmp_path):
    drv = Driver()
    be = NotebookLMBrowserBackend(drv)
    assert isinstance(be, LearningBackend) and be.name is BackendName.NOTEBOOKLM_BROWSER
    handle = be.create("https://example.invalid/talk", "ten minutes", "item1")
    assert handle.nb_id == "nlm1" and handle.ref == "art1"
    assert [c[0] for c in drv.calls] == ["create_notebook", "add_url_source", "start_audio_overview"]
    assert drv.calls[2][2] == "ten minutes"
    assert be.poll(handle).status is JobStatus.GENERATING
    drv.state = ("ready", None)
    assert be.poll(handle).status is JobStatus.READY
    drv.state = ("failed", "daily limit reached")
    failed = be.poll(handle)
    assert failed.status is JobStatus.FAILED and failed.reason == "daily limit reached"
    assert be.link("nlm1").endswith("/nlm1")
    assert be.fetch_audio(handle, tmp_path).read_bytes() == b"audio"


@pytest.mark.parametrize("step", ["create_notebook", "add_url_source", "start_audio_overview", "audio_state"])
def test_a_changed_ui_fails_fast_with_a_clear_error(step):
    """Edge case: NotebookLM UI change -> the backend fails with a message naming the step, never hangs."""
    drv = Driver()
    drv.broken_at = step
    be = NotebookLMBrowserBackend(drv)
    with pytest.raises(BackendUnavailable) as err:
        if step == "audio_state":
            be.poll(BackendHandle("n", "j", "a"))
        else:
            be.create("https://example.invalid/x", "", "i")
    msg = str(err.value)
    assert "NotebookLM browser automation failed" in msg and "may have changed" in msg
    assert "TimeoutError" in msg and "aria-label" not in msg  # the type, not page internals


def test_driver_is_loaded_from_configuration_only():
    with pytest.raises(BackendUnavailable, match="needs 'driver'"):
        build_backend("notebooklm_browser", {})
    with pytest.raises(BackendUnavailable, match="needs 'driver'"):
        NotebookLMBrowserBackend.from_config({"driver": "os.system"})  # not module:factory
    with pytest.raises(BackendUnavailable, match="could not be loaded"):
        NotebookLMBrowserBackend.from_config({"driver": "no_such_module_xyz:make"})
    be = NotebookLMBrowserBackend.from_config(
        {"driver": "tests.support.notebooklm_driver:make", "options": {"x": 1}}
    )
    assert be.driver.options == {"x": 1}


def test_unknown_backend_name_lists_the_known_ones():
    with pytest.raises(LearningError, match="open_notebook, notebooklm_browser, fake"):
        build_backend("notebooklm", {})


def test_a_hanging_browser_call_times_out_instead_of_blocking_the_gateway():
    """Edge case "fails fast": a driver call that never returns ends after ``call_timeout_s``."""
    import threading
    import time

    release = threading.Event()

    class Hanging(Driver):
        def add_url_source(self, notebook_id, url):
            release.wait(30)

    be = NotebookLMBrowserBackend.from_config(
        {"driver": "tests.support.notebooklm_driver:make", "call_timeout_s": 0.2}
    )
    be.driver = Hanging()
    start = time.monotonic()
    try:
        with pytest.raises(BackendUnavailable, match="timed out at 'add source' after 0.2 s"):
            be.create("https://example.invalid/x", "", "i")
    finally:
        release.set()
    assert time.monotonic() - start < 5
    with pytest.raises(BackendUnavailable, match="call_timeout_s must be positive"):
        NotebookLMBrowserBackend(Driver(), call_timeout_s=0)
