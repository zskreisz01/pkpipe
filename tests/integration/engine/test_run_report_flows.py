"""Spec 024: the report after every run and the todo block of the daily journal, end to end.

The pack's flows run on a synthetic vault with the real vault tools, the fake learning gateway and a fake
notify channel. The clock is the real one, because the vault tool only writes the journal of today or
yesterday."""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pytest

from pkpipe.domain.journal_todos import BEGIN, END
from pkpipe.domain.layout import VaultLayout
from pkpipe.domain.triage import item_id_for
from pkpipe.engine.registry import load_effective
from pkpipe.obsidian import lists
from pkpipe.obsidian.policy import VaultPolicy
from tests.fakes import FakeClock, FakeModel, FakeNotify, FakeProposalAttempts, RecordingTracer
from tests.support.flows import make_ports, make_service
from tests.support.pack_runtime import (
    PACK_ROUTES,
    TRIAGE_FILE,
    FakeLearning,
    fake_model_reply,
    owner_decides,
    synthetic_web,
    vault_tools,
)

TOOL = item_id_for("0-INBOX/example-tool.md")
VIDEO = item_id_for("0-INBOX/example-video.md")
TODAY = date.today().isoformat()
JOURNAL = f"2-Areas/Journal/{TODAY}.md"
REPLIES = {"CLI tool": "tool", "video": "learning-material"}


class Run:
    def __init__(
        self, vault: Path, pack_dir: Path, *, grant_journal: bool = True, report: dict | None = None
    ):
        self.vault = vault
        self.snap = load_effective(pack_dir, None).snapshot()
        if report is not None:
            self.snap.data["report"] = report
        allow = VaultLayout().default_allow()
        if grant_journal:
            allow["write_journal_todos"] = ["2-Areas/Journal"]
        policy = VaultPolicy(vault_dir=vault, allow=allow)
        self.notify = FakeNotify(routes=PACK_ROUTES)
        web = synthetic_web()
        ports = make_ports(
            proposal_attempts=FakeProposalAttempts(),
            tools=vault_tools(vault, FakeLearning(polls_until_ready=0), web, policy=policy),
            resolver=web.resolve,
            model=FakeModel(fake_model_reply(REPLIES)),
            notify=self.notify,
            tracer=RecordingTracer(),
            clock=FakeClock(time.time()),
        )
        self.svc = make_service(lambda: self.snap, ports)

    def journal(self) -> str:
        return (self.vault / JOURNAL).read_text(encoding="utf-8")


@pytest.fixture
def run(synthetic_vault, pack_dir):
    return Run(synthetic_vault, pack_dir)


def _journal_report(synthetic_vault, pack_dir):
    return Run(synthetic_vault, pack_dir, report={"journal_todos": {"enabled": True}})


def test_the_triage_run_sends_the_report_with_the_approval_todo(run):
    assert run.svc.start("triage").ok
    [sent] = run.notify.sent
    assert sent.event == "daily_summary" and sent.payload["title"] == "Triage run"
    msg = sent.message
    assert "**New suggestions (2):** learning-material 1, tool 1" in msg
    assert "**Waiting for you:** 2 decision(s) in the triage" in msg
    assert f"- {TODAY} - Review triage decisions - 2 items - [[0-INBOX/Inbox-Triage]]" in msg
    assert (
        sent.payload["new"] == 2
        and sent.payload["awaiting"] == 2
        and sent.payload["todos"] == {"approval": 2}
    )


def test_the_release_run_reports_what_it_processed_created_and_what_is_still_to_do(run):
    assert run.svc.start("triage").ok
    owner_decides(run.vault, confirm=[TOOL, VIDEO])
    done = run.svc.start("process_released")
    assert done.ok, done.error
    sent = run.notify.sent[-1]
    msg = sent.message
    assert sent.payload["title"] == "Release run" and sent.payload["processed"] == 2
    assert "**Processed (2):** learning-material 1, tool 1" in msg
    assert "**Created:** 1 evaluations · 1 notebooks" in msg
    assert (sent.payload["evaluations"], sent.payload["notebooks"]) == (1, 1)
    assert "**Waiting for you:** 0 decision(s)" in msg
    # the evaluation note of the tool category has not been read by the owner yet
    [note] = list((run.vault / "3-Resources/Tools").glob(f"*-{TOOL}.md"))
    assert f"- {TODAY} - Read tool evaluations - 1 item - [[3-Resources/Tools/{note.stem}]]" in msg
    assert "Review triage decisions" not in msg
    assert sent.payload["todos"] == {"tool_reads": 1}


def test_marking_the_evaluation_reviewed_removes_the_todo_at_the_next_run(run):
    assert run.svc.start("triage").ok
    owner_decides(run.vault, confirm=[TOOL])
    assert run.svc.start("process_released").ok
    assert "Read tool evaluations - 1 item" in run.notify.sent[-1].message
    [note] = list((run.vault / "3-Resources/Tools").glob(f"*-{TOOL}.md"))
    text = note.read_text(encoding="utf-8")
    note.write_text(text.replace("pkpipe_item:", "reviewed: true\npkpipe_item:", 1), encoding="utf-8")
    assert run.svc.start("process_released").ok
    assert "Read tool evaluations" not in run.notify.sent[-1].message
    assert "**Todo**" in run.notify.sent[-1].message or "nothing open" in run.notify.sent[-1].message


def test_a_confirmed_learning_item_is_a_notebook_todo_until_the_release_run_has_processed_it(run):
    assert run.svc.start("triage").ok
    owner_decides(run.vault, confirm=[VIDEO])
    assert run.svc.start("triage").ok  # an evening run before the 06:00 release
    msg = run.notify.sent[-1].message
    assert f"- {TODAY} - Process notebook materials and podcasts - 1 item - " in msg
    assert "[[0-INBOX/Manual-Resource-Processing-List]]" in msg and "Review triage decisions - 1 item" in msg
    assert run.svc.start("process_released").ok
    assert "Process notebook materials and podcasts" not in run.notify.sent[-1].message


def test_the_journal_gets_the_todo_block_when_it_is_switched_on_and_granted(synthetic_vault, pack_dir):
    run = _journal_report(synthetic_vault, pack_dir)
    assert run.svc.start("triage").ok
    text = run.journal()
    assert text == (
        f"---\ntype: journal\n---\n\n## pkpipe todo\n{BEGIN}\n"
        f"- {TODAY} - Review triage decisions - 2 items - [[0-INBOX/Inbox-Triage]]\n{END}\n"
    )


def test_a_rerun_replaces_the_block_and_the_owners_journal_text_stays(synthetic_vault, pack_dir):
    run = _journal_report(synthetic_vault, pack_dir)
    journal = synthetic_vault / JOURNAL
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("# today\n\n## Personal\nprivate words\n", encoding="utf-8")
    assert run.svc.start("triage").ok
    owner_decides(synthetic_vault, confirm=[TOOL, VIDEO])
    assert run.svc.start("process_released").ok
    text = run.journal()
    assert text.startswith("# today\n\n## Personal\nprivate words\n")
    assert text.count(BEGIN) == 1 and text.count("## pkpipe todo") == 1
    assert "Review triage decisions" not in text and "Read tool evaluations - 1 item" in text
    assert "private words" in text and "example.invalid" not in text


def test_the_journal_stays_untouched_when_the_option_is_off(run):
    assert run.svc.start("triage").ok
    assert not (run.vault / "2-Areas/Journal").exists()


def test_a_journal_that_the_policy_does_not_grant_fails_softly_and_the_report_is_still_sent(
    synthetic_vault, pack_dir
):
    run = Run(synthetic_vault, pack_dir, grant_journal=False, report={"journal_todos": {"enabled": True}})
    result = run.svc.start("triage")
    assert result.ok, result.error  # the report never fails a run
    assert "**New suggestions (2):**" in run.notify.sent[-1].message
    assert not (synthetic_vault / JOURNAL).exists()


def test_a_failing_report_subflow_never_fails_the_run(synthetic_vault, pack_dir, monkeypatch):
    run = Run(synthetic_vault, pack_dir)
    calls = {"n": 0}
    real = run.svc.ports_for(run.snap).tools  # the fake tools of the run
    original = real.tools["obsidian:read_triage"]

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] >= 2:  # the overview note's read (spec 033) and the report's own read come after
            raise RuntimeError("vault is gone")
        return original(**kw)

    real.tools["obsidian:read_triage"] = flaky
    result = run.svc.start("triage")
    assert result.ok, result.error
    assert calls["n"] == 3 and run.notify.sent == []  # the report was lost, the run was not


def test_report_off_keeps_the_earlier_one_line_messages(synthetic_vault, pack_dir):
    run = Run(synthetic_vault, pack_dir, report={"enabled": False})
    assert run.svc.start("triage").ok
    assert run.notify.sent[-1].event == "triage_ready" and run.notify.sent[-1].message.startswith(
        "Triage ready"
    )
    owner_decides(synthetic_vault, confirm=[TOOL])
    assert run.svc.start("process_released").ok
    assert run.notify.sent[-1].message.startswith("Processed 1, quarantined 0, failed 0")
    assert not (synthetic_vault / "2-Areas/Journal").exists()


def test_no_item_text_url_or_comment_reaches_the_report_or_the_journal(synthetic_vault, pack_dir):
    run = _journal_report(synthetic_vault, pack_dir)
    assert run.svc.start("triage").ok
    rows = lists.read_triage(synthetic_vault / TRIAGE_FILE)["rows"]
    assert rows
    owner_decides(synthetic_vault, confirm=[TOOL, VIDEO])
    assert run.svc.start("process_released").ok
    everything = "\n".join(
        [m.message for m in run.notify.sent] + [repr(m.payload) for m in run.notify.sent] + [run.journal()]
    )
    for forbidden in ("example.invalid", "fictional CLI tool", "worth a notebook", "example-video"):
        assert forbidden not in everything
    # the one name of the pipeline's own tool note may appear, and only as a link to that note
    assert everything.count("example-tool") == everything.count("[[3-Resources/Tools/example-tool")
    assert len(run.notify.sent) == 2 and all(len(m.message) <= 1800 for m in run.notify.sent)


def test_the_vault_server_exposes_the_two_report_tools_in_the_packs_allow_list(pack_dir):
    allow = load_effective(pack_dir, None).snapshot().data["mcp_servers"]["obsidian"]["allow"]
    assert {"unreviewed_notes", "write_journal_todos"} <= set(allow)
