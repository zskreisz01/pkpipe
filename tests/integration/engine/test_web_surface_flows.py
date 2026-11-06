"""Spec 023 US3 on the real flows: the ``web`` surface keeps the table in the data file, the triage file is a
generated summary page, the first triage run after a switch moves a live-shaped table losslessly, and the
06:00 release reads and writes the data file. Real vault files, fake model and ports."""

from __future__ import annotations

import dataclasses

import pytest

from pkpipe.domain.triage import item_id_for
from pkpipe.engine.registry import load_effective
from pkpipe.obsidian import lists
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.surface import has_table
from pkpipe.obsidian.vault import Vault
from tests.fakes import FakeClock, FakeModel, FakeNotify, RecordingTracer
from tests.support.flows import make_ports, make_service
from tests.support.pack_runtime import (
    PACK_ROUTES,
    FakeLearning,
    fake_model_reply,
    synthetic_web,
    vault_tools,
)

PAGE = "0-INBOX/Inbox-Triage.md"
DATA = "0-INBOX/_pkpipe/triage-data.md"
URL = "https://mini.tail1234.ts.net"
TOOL = item_id_for("0-INBOX/example-tool.md")
OLD_ROWS = [
    f"| [ x] | {i:012x} | https://example.org/old/{i} | misc |  | processed |  |  |  | note {i} |"
    for i in range(1, 6)
]
HEADER = (
    "| confirm | item | resource | suggested | override | state | kind | needs_owner | topic | comment |\n"
    "|" + " --- |" * 10 + "\n"
)


class Web:
    def __init__(self, vault, pack_dir, surface):
        self.vault = vault
        policy = VaultPolicy(vault_dir=vault, triage_surface=surface, ui_public_url=URL)
        self.owner = Vault(policy)
        data = {
            "triage": {"surface": surface},
            "ui": {"public_url": URL, "allowed_hosts": []},
        }
        base = load_effective(pack_dir, None).snapshot()
        self.snap = dataclasses.replace(base, data={**base.data, **data})
        self.model = FakeModel(fake_model_reply({"CLI tool": "tool", "video": "learning-material"}))
        self.notify = FakeNotify(routes=PACK_ROUTES)
        web = synthetic_web()
        ports = make_ports(
            tools=vault_tools(vault, FakeLearning(polls_until_ready=0), web, policy=policy),
            resolver=web.resolve,
            model=self.model,
            notify=self.notify,
            tracer=RecordingTracer(),
            clock=FakeClock(),
        )
        self.svc = make_service(lambda: self.snap, ports)


@pytest.fixture
def old_page(synthetic_vault):
    text = "# Inbox triage\n\nTick confirm.\n\n" + HEADER + "\n".join(OLD_ROWS) + "\n"
    (synthetic_vault / PAGE).write_text(text, encoding="utf-8")
    return synthetic_vault


def test_the_first_triage_run_after_the_switch_moves_the_table_and_the_next_one_changes_nothing(
    old_page, pack_dir
):
    w = Web(old_page, pack_dir, "web")
    run = w.svc.start("triage")
    assert run.ok, run.error
    data = (old_page / DATA).read_text(encoding="utf-8")
    page = (old_page / PAGE).read_text(encoding="utf-8")
    assert all(row in data for row in OLD_ROWS)  # every old row verbatim, the stray `[ x]` included
    assert has_table(data) and not has_table(page) and "|" not in page.replace("·", "")
    rows = {r["item_id"] for r in lists.read_triage(old_page / DATA)["rows"]}
    assert TOOL in rows and len(rows) == 5 + 2  # the old rows plus the two new INBOX notes
    assert run.outputs["migrate"]["migration"]["rows"] == 5
    assert "Waiting for your decision: 2" in page and URL in page
    again = w.svc.start("triage")
    assert again.ok and again.outputs["migrate"]["migration"] is None
    assert (old_page / DATA).read_text(encoding="utf-8") == data  # idempotent


def test_the_release_reads_and_writes_the_data_file_and_keeps_the_summary_current(old_page, pack_dir):
    w = Web(old_page, pack_dir, "web")
    assert w.svc.start("triage").ok
    w.owner.owner_decide(TOOL, override="misc")  # what the web UI does
    run = w.svc.start("process_released")
    assert run.ok, run.error
    state = {r["item_id"]: r["state"] for r in lists.read_triage(old_page / DATA)["rows"]}
    assert state[TOOL] == "processed"
    assert not has_table((old_page / PAGE).read_text(encoding="utf-8"))
    assert "Waiting for your decision: 1" in (old_page / PAGE).read_text(encoding="utf-8")


def test_back_to_the_vault_surface_restores_the_table_in_the_triage_file(old_page, pack_dir):
    web = Web(old_page, pack_dir, "web")
    assert web.svc.start("triage").ok
    before = [r for r in lists.read_triage(old_page / DATA)["rows"]]
    vault = Web(old_page, pack_dir, "vault")
    run = vault.svc.start("triage")
    assert run.ok, run.error
    assert run.outputs["migrate"]["migration"]["direction"] == "to_vault"
    after = lists.read_triage(old_page / PAGE)["rows"]
    assert {r["item_id"] for r in before} <= {r["item_id"] for r in after}
    assert not has_table((old_page / DATA).read_text(encoding="utf-8"))
    assert all(row in (old_page / PAGE).read_text(encoding="utf-8") for row in OLD_ROWS)


def test_the_evening_message_points_to_the_web_ui_on_the_web_surface(old_page, pack_dir):
    w = Web(old_page, pack_dir, "web")
    assert w.svc.start("triage").ok
    messages = [s.payload for s in w.notify.sent] + [getattr(s, "message", "") for s in w.notify.sent]
    text = repr(messages)
    assert "the web UI" in text and PAGE not in text.replace("\\\\", "")
