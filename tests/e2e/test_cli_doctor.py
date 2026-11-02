"""``pkpipe doctor`` and the preflight of ``pkpipe eval`` end to end (docs/operations.md "System preflight"):
the real probe on this machine as a subprocess, and the CLI entry point with a fake probe forcing ok,
degraded and blocked states."""

from __future__ import annotations

import json

import pytest

from pkpipe.cli import main
from pkpipe.system import cli as doctor_cli
from tests.e2e.test_cli_validate import UV, pkpipe
from tests.fakes import FakeSystemProbe
from tests.fakes.system import healthy_snapshot

NO_MODEL = {"PKPIPE_MODEL_BASE_URL": "http://127.0.0.1:9/v1"}  # nothing listens there
PACK_MODELS = ("qwen3.5-9b-mlx", "google/gemma-4-12b")


@pytest.mark.skipif(UV is None, reason="uv not installed")
def test_doctor_on_this_machine_prints_snapshot_and_decision():
    r = pkpipe("doctor", "--no-llm")
    assert r.returncode in (0, 1), r.stderr
    for label in ("system", "memory", "swap", "thermal", "load", "top memory", "decision"):
        assert any(line.startswith(label) for line in r.stdout.splitlines()), (label, r.stdout)
    assert ("BLOCKED" in r.stdout) == (r.returncode == 1)
    js = pkpipe("doctor", "--no-llm", "--json")
    data = json.loads(js.stdout)
    assert data["snapshot"]["memory"]["total_bytes"] > 0 and data["snapshot"]["llm"] is None
    assert (
        data["decision"]["status"] in ("ok", "degraded", "blocked") and data["decision"]["concurrency"] >= 0
    )
    assert all("/" not in p["name"] for p in data["snapshot"]["processes"])


@pytest.mark.skipif(UV is None, reason="uv not installed")
def test_doctor_blocks_when_the_llm_server_is_unreachable():
    r = pkpipe("doctor", "--json", env_extra=NO_MODEL)
    assert r.returncode == 1, r.stderr
    data = json.loads(r.stdout)
    assert data["decision"]["status"] == "blocked"
    assert [x["code"] for x in data["decision"]["reasons"] if x["severity"] == "block"] == ["llm_unreachable"]
    assert data["required_models"] == list(PACK_MODELS)


@pytest.fixture
def fake_probe(monkeypatch):
    probe = FakeSystemProbe()
    urls: list = []

    def factory(base_url):
        urls.append(base_url)
        return probe

    monkeypatch.setattr(doctor_cli, "probe_factory", factory)
    probe.urls = urls
    return probe


def _served(**changes):
    """A healthy machine serving the pack's model and judge."""
    snap = healthy_snapshot(**changes)
    llm = snap.llm
    from dataclasses import replace

    models = tuple(replace(m, id=name) for m, name in zip(llm.models, PACK_MODELS, strict=False))
    return replace(snap, llm=replace(llm, models=models))


@pytest.mark.parametrize(
    ("changes", "code", "status", "words"),
    [
        ({}, 0, "ok", ["OK: eval concurrency 4, timeouts x1"]),
        (
            {"memory": {"pressure": "warn"}},
            0,
            "degraded",
            ["DEGRADED: eval concurrency 2", "memory_pressure"],
        ),
        (
            {"thermal": {"level": "serious"}},
            0,
            "degraded",
            ["eval concurrency 1, timeouts x2", "thermal state serious"],
        ),
        ({"memory": {"pressure": "critical"}}, 1, "blocked", ["BLOCKED", "[block] memory_pressure"]),
    ],
    ids=["ok", "degraded", "minimal", "blocked"],
)
def test_doctor_with_a_forced_state(fake_probe, capsys, changes, code, status, words):
    fake_probe.set(_served(**changes))
    assert main(["doctor"]) == code
    out = capsys.readouterr().out
    for w in words:
        assert w in out, out
    assert "model-under-test" not in out and "qwen3.5-9b-mlx (required): loaded, parallel 4" in out
    assert main(["doctor", "--json"]) == code
    assert json.loads(capsys.readouterr().out)["decision"]["status"] == status


def test_doctor_options(fake_probe, capsys):
    fake_probe.set(_served())
    assert main(["doctor", "--max-concurrency", "8", "--model", "other", "--base-url", "http://h:1/v1"]) == 1
    out = capsys.readouterr().out
    assert "model 'other' is not served" in out and fake_probe.urls[-1] == "http://h:1/v1"
    assert main(["doctor", "--no-llm", "--max-concurrency", "8"]) == 0
    assert "eval concurrency 8" in capsys.readouterr().out
    assert fake_probe.calls[-1] == {"include_llm": False} and fake_probe.urls[-1] is None


class _Stop(Exception):
    pass


@pytest.fixture
def captured_runner(monkeypatch):
    """Stops ``pkpipe eval`` right before the suites run and keeps what it would have used."""
    from pkpipe.evals.runner import EvalRunner

    seen = {}

    def run(self, names):
        seen.update(
            concurrency=self.concurrency,
            multiplier=getattr(self.model, "timeout_multiplier", None),
            names=names,
        )
        raise _Stop

    monkeypatch.setattr(EvalRunner, "run", run)
    return seen


def test_eval_without_concurrency_uses_the_preflight_and_never_raises_the_production_default(
    fake_probe, captured_runner, capsys, overlay_dir
):
    """Production default evals.concurrency = 1 stays 1 on a healthy machine; a degraded machine scales the
    model timeouts; an overlay that allows 4 gets 2 under memory pressure."""
    fake_probe.set(_served())
    with pytest.raises(_Stop):
        main(["eval", "--suite", "triage"])
    assert captured_runner["concurrency"] == 1 and captured_runner["multiplier"] == 1.0
    fake_probe.set(_served(thermal={"level": "serious"}))
    with pytest.raises(_Stop):
        main(["eval", "--suite", "triage"])
    assert captured_runner["concurrency"] == 1 and captured_runner["multiplier"] == 2.0
    assert "preflight: degraded" in capsys.readouterr().err
    (overlay_dir / "evals.json").write_text(json.dumps({"concurrency": 4}))
    fake_probe.set(_served(memory={"pressure": "warn"}))
    with pytest.raises(_Stop):
        main(["eval", "--suite", "triage", "--overlay", str(overlay_dir)])
    assert captured_runner["concurrency"] == 2 and captured_runner["multiplier"] == 1.5


def test_eval_is_refused_on_a_system_block_and_explicit_choices_skip_the_preflight(
    fake_probe, captured_runner, capsys
):
    fake_probe.set(_served(thermal={"level": "critical"}))
    assert main(["eval", "--suite", "triage"]) == 1
    assert "preflight: blocked" in capsys.readouterr().err and "concurrency" not in captured_runner
    calls = len(fake_probe.calls)
    for argv in (["--concurrency", "3"], ["--no-preflight"], ["--cassette", "replay"]):
        with pytest.raises(_Stop):
            main(["eval", "--suite", "triage", *argv])
    assert len(fake_probe.calls) == calls  # none of them probed
    with pytest.raises(_Stop):
        main(["eval", "--suite", "deterministic,flows"])  # code-only suites need no model
    assert len(fake_probe.calls) == calls


def test_eval_leaves_an_unreachable_model_to_the_suites(fake_probe, captured_runner, capsys):
    """An unreachable server is an llm-scope block: the suites report it per case; the run is not refused."""
    fake_probe.set(_served(llm={"reachable": False, "models": ()}))
    with pytest.raises(_Stop):
        main(["eval", "--suite", "triage"])
    err = capsys.readouterr().err
    assert "preflight: blocked" in err and "LLM server not reachable" in err and "--no-preflight" not in err
    assert captured_runner["concurrency"] == 1 and captured_runner["multiplier"] is None  # evals.concurrency
