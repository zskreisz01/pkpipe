"""Spec 033: the pack's triage and release flows refresh the pipeline overview note; a failing write never
fails the run, and a refresh that finds nothing new leaves the note alone."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from pkpipe.domain import pipeline as pl
from pkpipe.domain.layout import VaultLayout
from pkpipe.domain.triage import item_id_for
from pkpipe.engine.registry import load_effective
from pkpipe.obsidian.policy import VaultPolicy
from tests.fakes import (
    FakeClock,
    FakeModel,
    FakeNotify,
    FakePipelineStats,
    FakeProposalAttempts,
    RecordingTracer,
)
from tests.support.flows import make_ports, make_service
from tests.support.pack_runtime import (
    PACK_ROUTES,
    FakeLearning,
    fake_model_reply,
    owner_decides,
    synthetic_web,
    vault_tools,
)

NOTE = "0-INBOX/Pipeline.md"
TOOL = item_id_for("0-INBOX/example-tool.md")
REPLIES = {"CLI tool": "tool", "video": "learning-material"}


class Run:
    def __init__(self, vault: Path, pack_dir: Path, *, enabled: bool = True, stats=None):
        self.vault = vault
        self.snap = load_effective(pack_dir, None).snapshot()
        self.snap.data.setdefault("triage", {}).setdefault("pipeline", {})["enabled"] = enabled
        self.policy = VaultPolicy(vault_dir=vault, allow=VaultLayout().default_allow())
        self.tools = vault_tools(
            vault, FakeLearning(polls_until_ready=0), synthetic_web(), policy=self.policy
        )
        web = synthetic_web()
        self.ports = make_ports(
            proposal_attempts=FakeProposalAttempts(),
            tools=self.tools,
            resolver=web.resolve,
            model=FakeModel(fake_model_reply(REPLIES)),
            notify=FakeNotify(routes=PACK_ROUTES),
            tracer=RecordingTracer(),
            clock=FakeClock(time.time()),
            pipeline_stats=stats or FakePipelineStats(),
            next_runs=lambda schedules, now: [
                {"flow": "triage", "cron": "0 20 * * *", "at": "2026-11-10 20:00"}
            ],
        )
        self.svc = make_service(lambda: self.snap, self.ports)

    def note(self) -> str:
        return (self.vault / NOTE).read_text(encoding="utf-8")


def test_a_triage_run_writes_the_note_with_the_new_items(synthetic_vault, pack_dir):
    run = Run(synthetic_vault, pack_dir)
    assert run.svc.start("triage").ok
    text = run.note()
    assert text.startswith(pl.HEADING)
    assert "## Funnel" in text and "waiting for your confirmation" in text
    assert "next run" in text and "triage" in text  # the schedule helper was used
    assert "example-tool" not in text and "IGNORE" not in text  # no note text, only ids and codes


def test_a_second_run_that_finds_nothing_new_leaves_the_note_untouched(synthetic_vault, pack_dir):
    run = Run(synthetic_vault, pack_dir)
    assert run.svc.start("triage").ok
    first = (synthetic_vault / NOTE).stat().st_mtime_ns
    text = run.note()
    time.sleep(0.01)
    assert run.svc.start("triage").ok
    assert run.note() == text and (synthetic_vault / NOTE).stat().st_mtime_ns == first


def test_a_release_run_updates_the_note(synthetic_vault, pack_dir):
    run = Run(synthetic_vault, pack_dir)
    assert run.svc.start("triage").ok
    before = run.note()
    owner_decides(synthetic_vault, confirm=[TOOL])
    assert run.svc.start("process_released").ok
    after = run.note()
    assert pl.normalize(after) != pl.normalize(before)  # the funnel moved on
    assert "processed" in after.lower()


def test_the_switch_turns_the_note_off(synthetic_vault, pack_dir):
    run = Run(synthetic_vault, pack_dir, enabled=False)
    assert run.svc.start("triage").ok
    assert not (synthetic_vault / NOTE).exists()


@pytest.mark.parametrize("broken", ["obsidian:write_pipeline", "obsidian:read_processing_list"])
def test_a_failing_vault_call_never_fails_the_run(synthetic_vault, pack_dir, broken):
    run = Run(synthetic_vault, pack_dir)

    def boom(**kw):
        raise RuntimeError("vault is gone")

    run.tools.tools[broken] = boom
    result = run.svc.start("triage")
    assert result.ok, result.error
    assert not (synthetic_vault / NOTE).exists()
