"""Spec 032: the shipped ``nlm`` CLI driver (a scripted runner; no CLI, no Google account)."""

from __future__ import annotations

import json

import pytest

from pkpipe.learning.drivers.nlm_cli import NlmDriver, NlmError, _run


class Runner:
    def __init__(self, answers: dict[str, object]):
        self.answers, self.calls = answers, []

    def __call__(self, args, timeout):
        self.calls.append(args)
        key = " ".join(args[:2])
        value = self.answers[key]
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, str) else json.dumps(value)


def test_usage_is_the_json_of_nlm_usage():
    data = {
        "windows": [
            {"window": "rolling", "percent_remaining": 96.0, "resets_at": "2026-11-10T10:37:29+00:00"}
        ]
    }
    assert NlmDriver(Runner({"usage --json": data})).usage() == data
    assert NlmDriver(Runner({"usage --json": "[]"})).usage() == {}  # not an object: no quota information


def test_the_audio_overview_is_started_with_the_template_as_focus_and_read_back():
    run = Runner(
        {
            "studio status": [{"type": "audio", "artifact_id": "old", "status": "completed"}],
            "audio create": {"artifact_id": "new1"},
        }
    )
    drv = NlmDriver(run)
    assert drv.start_audio_overview("nb1", "ten minutes please") == "new1"
    assert "--focus" in run.calls[-1] and "ten minutes please" in run.calls[-1]


def test_a_missing_artifact_id_is_found_by_comparing_the_artifact_lists():
    class Seq(Runner):
        def __call__(self, args, timeout):
            self.calls.append(args)
            if args[0] == "audio":
                return "not json"
            seen = sum(1 for c in self.calls if c[0] == "studio")
            return json.dumps(
                [{"type": "audio", "id": "a1"}] + ([{"type": "audio", "id": "a2"}] if seen > 1 else [])
            )

    assert NlmDriver(Seq({})).start_audio_overview("nb", "") == "a2"


@pytest.mark.parametrize(
    "status, state",
    [("completed", "ready"), ("failed", "failed"), ("in_progress", "generating")],
)
def test_the_audio_state_follows_the_artifact_status(status, state):
    run = Runner({"studio status": [{"type": "audio", "artifact_id": "a", "status": status}]})
    assert NlmDriver(run).audio_state("nb", "a")[0] == state
    assert NlmDriver(run).audio_state("nb", "gone")[0] == "failed"


def test_the_notebook_link_is_the_phone_reachable_google_address():
    assert NlmDriver(Runner({})).notebook_url("abc") == "https://notebook.google.com/notebook/abc"


def test_the_runner_keeps_only_the_last_line_of_a_failure(monkeypatch, tmp_path):
    class Proc:
        returncode, stdout, stderr = 1, "", "Traceback...\nlong\nAudio generation limit reached"

    monkeypatch.setattr("subprocess.run", lambda *a, **k: Proc())
    with pytest.raises(NlmError, match="limit reached"):
        _run(["audio", "create"], 1.0)
