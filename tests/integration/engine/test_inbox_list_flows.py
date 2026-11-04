"""Spec 020: the list-style INBOX through triage and release, on a vault with the owner's real layout.

Only the policy and the ``layout.json`` overlay differ from the default pack run (US2): the folders are
``1-Inbox``, ``4-Resources``, ``6-Archive`` and the journals live under ``DailyJournals/<year>/``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pkpipe.domain.inbox_list import parse_list
from pkpipe.domain.layout import VaultLayout
from pkpipe.domain.triage import item_id_for
from pkpipe.engine.registry import load_effective
from pkpipe.obsidian import lists
from pkpipe.obsidian.policy import VaultPolicy
from tests.fakes import FakeClock, FakeModel, FakeNotify, RecordingTracer
from tests.support.flows import make_ports, make_service
from tests.support.pack_runtime import (
    PACK_ROUTES,
    FakeLearning,
    fake_model_reply,
    synthetic_web,
    vault_tools,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "inbox" / "INBOX-list.md"
REAL_LAYOUT = {
    "inbox_dir": "1-Inbox",
    "inbox_list_file": "1-Inbox/INBOX.md",
    "triage_file": "1-Inbox/Inbox-Triage.md",
    "processing_list": "1-Inbox/Manual-Resource-Processing-List.md",
    "archive_dir": "6-Archive/Inbox",
    "inbox_archive_pattern": "6-Archive/Inbox/Inbox-Archive-{yyyy-MM-dd}.md",
    "agent_tasks_dir": "3-Areas/Agent-Tasks",
    "tool_candidates_file": "3-Areas/System/System-Tool-Candidates.md",
    "improvement_file": "3-Areas/Self-Improvement/Improvement-Backlog.md",
    "proposals_dir": "3-Areas/Self-Improvement/System-Changes",
    "quarantine_dir": "1-Inbox/_quarantine",
    "journal_pattern": "DailyJournals/{yyyy}/{yyyy-MM-dd}.md",
    "attachments_dir": "1-Inbox/attachments",
    "projects_dir": "2-Projects",
    "areas_dir": "3-Areas",
    "resources_dir": "4-Resources",
    "context_review_dir": "3-Areas/Context-Review",
}
_PARA = ["1-Inbox", "2-Projects", "3-Areas", "4-Resources", "6-Archive", "DailyJournals"]
ALLOW = {"read_notes": _PARA, "read_note": _PARA}
ALPHA = item_id_for("https://example.org/articles/alpha")
IMAGE = item_id_for("IMG_20260101_0001.jpg")
TALK = item_id_for("https://example.org/talks/local-first")
NOTE = item_id_for("remember to compare the two note taking approaches")


@pytest.fixture
def overlay(tmp_path):
    ov = tmp_path / "overlay"
    ov.mkdir()
    (ov / "layout.json").write_text(json.dumps(REAL_LAYOUT))
    # the PARA folder of a category is category config: the overlay moves it with the folders
    paths = {"learning-material": "Learning", "tool": "Tools", "marketing": "Marketing", "misc": "Misc"}
    (ov / "categories.json").write_text(
        json.dumps([{"id": k, "para_path": f"4-Resources/{v}"} for k, v in paths.items()])
    )
    return ov


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    for f in ("1-Inbox", "6-Archive/Inbox", "4-Resources", "3-Areas"):
        (root / f).mkdir(parents=True)
    (root / "1-Inbox" / "INBOX.md").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return root


class World:
    def __init__(self, vault: Path, pack_dir: Path, overlay: Path, replies: dict[str, str]):
        self.vault = vault
        self.snap = load_effective(pack_dir, overlay).snapshot()
        allow = {**VaultLayout(**REAL_LAYOUT).default_allow(), **ALLOW}
        policy = VaultPolicy(vault_dir=vault, layout=REAL_LAYOUT, allow=allow)
        self.model = FakeModel(fake_model_reply(replies))
        self.notify = FakeNotify(routes=PACK_ROUTES)
        self.learning = FakeLearning(polls_until_ready=0)
        ports = make_ports(
            tools=vault_tools(vault, self.learning, synthetic_web(), policy=policy),
            resolver=synthetic_web().resolve,
            model=self.model,
            notify=self.notify,
            tracer=RecordingTracer(),
            clock=FakeClock(),
        )
        self.svc = make_service(lambda: self.snap, ports)

    @property
    def triage(self) -> dict[str, dict]:
        return {r["item_id"]: r for r in lists.read_triage(self.vault / "1-Inbox/Inbox-Triage.md")["rows"]}

    def listfile(self) -> str:
        return (self.vault / "1-Inbox/INBOX.md").read_text(encoding="utf-8")

    def decide(self, item_id: str, **kw) -> None:
        lists.owner_decide(self.vault / "1-Inbox/Inbox-Triage.md", item_id, **kw)


@pytest.fixture
def world(vault, pack_dir, overlay):
    return World(
        vault,
        pack_dir,
        overlay,
        {"alpha": "learning-material", "local-first": "learning-material", "compare": "misc"},
    )


def test_each_entry_is_one_item_and_images_wait_for_the_owner(world):
    """US1: the fixture list yields one row per resource; the image has no model call and no suggestion."""
    run = world.svc.start("triage")
    assert run.ok, run.error
    rows = world.triage
    assert set(rows) == {ALPHA, IMAGE, TALK, NOTE} | {
        item_id_for(r)
        for r in (
            "https://example.net/guide/setup-tips?ref=home",
            "Screenshot 2026-01-02 at 10.00.00.png",
            "Quick idea about weekly reviews that continues on a second line",
            "https://example.com/tool?version=2",
            "https://example.org/md-link",
        )
    }
    assert rows[ALPHA]["kind"] == "link" and rows[ALPHA]["ai_suggested_category"] == "learning-material"
    assert rows[IMAGE]["kind"] == "image" and rows[IMAGE]["needs_owner"] is True
    assert rows[IMAGE]["ai_suggested_category"] == "none"
    assert (
        rows[TALK]["kind"] == "text_link"
        and rows[TALK]["resource"] == "https://example.org/talks/local-first"
    )
    assert len(world.model.calls) == 7  # nine items, two images are not sent to the model
    assert "2 image item(s) need a category from you" in world.notify.sent[-1].message
    assert world.listfile() == FIXTURE.read_text(encoding="utf-8")  # triage never edits the list


def test_a_second_triage_selects_nothing_new_and_never_retriages_images(world):
    """US1 scenario 4."""
    assert world.svc.start("triage").ok
    calls = len(world.model.calls)
    again = world.svc.start("triage")
    assert again.ok and again.outputs["select_new"]["count"] == 0
    assert again.outputs["select_new"]["retriage"] == 0 and len(world.model.calls) == calls


def test_a_new_entry_added_meanwhile_is_picked_up_by_the_next_triage(world):
    assert world.svc.start("triage").ok
    path = world.vault / "1-Inbox/INBOX.md"
    path.write_text(path.read_text(encoding="utf-8") + "- https://example.org/new-one?utm_source=x\n")
    again = world.svc.start("triage")
    assert [i["resource"] for i in again.outputs["select_new"]["items"]] == ["https://example.org/new-one"]


def test_release_processes_the_item_and_removes_its_entries_into_the_archive(world):
    """US3: both duplicate entries leave the list and land in the dated archive; the rest is untouched."""
    assert world.svc.start("triage").ok
    before = parse_list(world.listfile())
    world.decide(ALPHA, confirm=True)
    run = world.svc.start("process_released")
    assert run.ok, run.error
    assert world.triage[ALPHA]["state"] == "processed"
    assert world.learning.created == [ALPHA]
    left = [e.raw for e in parse_list(world.listfile())]
    assert left == [e.raw for e in before if e.item_id != ALPHA]
    assert "<!-- owner note: check the speaker's other talks -->" in world.listfile()
    archives = list((world.vault / "6-Archive/Inbox").glob("Inbox-Archive-*.md"))
    assert len(archives) == 1
    text = archives[0].read_text(encoding="utf-8")
    assert text.count("- https://example.org/articles/alpha") == 2
    assert world.triage[IMAGE]["state"] == "pending"  # untouched: still the owner's call


def test_an_image_the_owner_categorises_is_released_like_any_item(world):
    """T004: the owner's override is the category; the entry is consumed once the item is processed."""
    assert world.svc.start("triage").ok
    world.decide(IMAGE, override="misc")
    run = world.svc.start("process_released")
    assert run.ok, run.error
    assert world.triage[IMAGE]["state"] == "processed"
    assert list((world.vault / "4-Resources/Misc").glob(f"*-{IMAGE}.md"))
    assert "IMG_20260101_0001.jpg" not in world.listfile()  # both duplicate embeds are gone
    archive = next((world.vault / "6-Archive/Inbox").glob("Inbox-Archive-*.md")).read_text(encoding="utf-8")
    assert archive.count("![[IMG_20260101_0001.jpg]]") == 2


def test_the_journal_pattern_opens_the_year_folder(world):
    """US2 scenario 2 / T007: the improvement flow reads DailyJournals/<year>/<date>.md."""
    day = world.vault / "DailyJournals" / "2026"
    day.mkdir(parents=True)
    (day / "2026-11-03.md").write_text("## Improvement\nStretch every morning.\n", encoding="utf-8")
    run = world.svc.start("improvement_extract", inputs={"date": "2026-11-03"})
    assert run.outputs["locate"]["path"] == "DailyJournals/2026/2026-11-03.md"
    assert run.outputs["read"]["body"].startswith("## Improvement")
