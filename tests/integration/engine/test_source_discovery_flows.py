"""Spec 025 end to end: an item without a source URL is searched for, fetched and evaluated.

The pack's ``triage`` and ``process_released`` flows on a temp vault with fake ports: the fake vision model
describes the images, the owner overrides the category to ``tool``, the release finds no URL and (when the
overlay turns ``source_discovery`` on) searches the web through the fake search tool, fetches the found pages
through the real secure-fetch steps and runs the category's own sessions.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pkpipe.domain.triage import item_id_for
from pkpipe.obsidian import lists
from tests.fakes import FakeSearch, FakeWeb
from tests.fakes.vision import valid_description
from tests.support.image_world import World as BaseWorld

ATT = "0-INBOX/attachments"
JPG = b"\xff\xd8\xff\xe0"
IMAGES = {
    "shot.jpg": JPG + b"a synthetic screenshot of a tool",
    "withurl.jpg": JPG + b"a synthetic screenshot that shows an address",
    "blank.jpg": JPG + b"a synthetic screenshot with nothing readable",
}
TEXT_ENTRY = "look into that sqlite vector search extension someday"
PAGE_1, PAGE_2 = "https://one.example/vectorlite", "https://two.example/vectorlite-docs"
PAGES = {
    PAGE_1: "<html><head><title>vectorlite</title></head>"
    "<body><p>vectorlite adds vector search to SQLite.</p>"
    "</body></html>",
    PAGE_2: "<html><head><title>vectorlite docs</title></head><body><p>Docs of vectorlite.</p></body></html>",
    "https://direct.example/tool": "<html><head><title>direct</title></head>"
    "<body><p>A direct page.</p></body></html>",
}
RESULTS = [
    {"title": "vectorlite", "url": PAGE_1, "snippet": "s"},
    {"title": "docs", "url": PAGE_2, "snippet": "s"},
]
QUERY = "A synthetic screenshot of the Vectorlite tool vector search for SQLite"


def digest(name: str) -> str:
    return hashlib.sha256(IMAGES[name]).hexdigest()


def script() -> dict:
    return {
        digest("shot.jpg"): valid_description(
            topic="A synthetic screenshot of the Vectorlite tool.", visible_text="vector search for SQLite"
        ),
        digest("withurl.jpg"): valid_description(
            topic="A synthetic screenshot of a direct tool page.",
            visible_text="Direct Tool",
            urls=["https://direct.example/tool"],
        ),
        digest("blank.jpg"): valid_description(topic="???", visible_text=""),
    }


def rid(resource: str) -> str:
    return item_id_for(resource)


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    for f in ("0-INBOX", "4-Archive/Inbox", "3-Resources", "2-Areas", ATT):
        (root / f).mkdir(parents=True)
    entries = [f"- ![[{n}]]" for n in IMAGES] + [f"- {TEXT_ENTRY}"]
    (root / "0-INBOX" / "INBOX.md").write_text("# Inbox\n\n" + "\n".join(entries) + "\n", encoding="utf-8")
    for name, data in IMAGES.items():
        (root / ATT / name).write_bytes(data)
    return root


class World(BaseWorld):
    """The image world with the web tools of this module (search results, pages, a resolver for them)."""

    def __init__(self, vault, pack_dir, overlay, *, results=None, pages=None):
        super().__init__(vault, pack_dir, overlay, vision_script=script())
        self.web = FakeWeb(PAGES if pages is None else pages)
        self.search = FakeSearch(RESULTS if results is None else results)
        self.ports.tools.tools["websearch:search"] = self.search.search
        self.ports.tools.tools["webfetch:fetch_page"] = self.web.fetch_page
        self.ports.extra["resolver"] = lambda host: [
            "93.184.216.34"
        ]  # every host is public; the pages decide
        self.triage_path = vault / "0-INBOX" / "Inbox-Triage.md"

    def release(self, name_or_text: str, category: str = "tool"):
        assert self.run("triage")
        item = rid(name_or_text)
        lists.owner_decide(self.triage_path, item, override=category)
        run = self.svc.start("process_released")
        assert run.ok, run.error
        return item

    def plist(self) -> dict:
        path = self.vault / "0-INBOX" / "Manual-Resource-Processing-List.md"
        return {r.item_id: r for r in lists.read_processing_list(path)} if path.exists() else {}

    def notes_of(self, item: str) -> list[Path]:
        return [p for p in self.vault.rglob(f"*-{item}.md") if "0-INBOX" not in p.parts]


def _overlay(tmp_path: Path, discovery: dict | None, security: dict | None = None) -> Path:
    ov = tmp_path / "overlay"
    ov.mkdir()
    (ov / "layout.json").write_text(json.dumps({"inbox_list_file": "0-INBOX/INBOX.md"}))
    if discovery is not None:
        (ov / "source_discovery.json").write_text(json.dumps(discovery))
    if security is not None:
        (ov / "security.json").write_text(json.dumps(security))
    return ov


@pytest.fixture
def make(tmp_path, pack_dir):
    def build(discovery=None, security=None, **kw):
        vault = _vault(tmp_path)
        return World(vault, pack_dir, _overlay(tmp_path, discovery, security), **kw)

    return build


ON = {"enabled": True, "categories": ["tool"]}


def test_an_image_without_an_address_is_searched_fetched_and_evaluated(make):
    w = make(ON)
    item = w.release("shot.jpg")
    assert w.search.queries[0] == QUERY
    assert w.web.fetched == [PAGE_1]
    row = w.plist()[item]
    assert row.status == "done", row
    [note] = w.notes_of(item)
    assert "Tools" in str(note)
    assert lists.read_triage(w.triage_path)["rows"]  # the table is intact


def test_the_next_candidate_is_tried_when_a_found_page_cannot_be_fetched(make):
    pages = {PAGE_2: PAGES[PAGE_2]}  # the first result is a 404
    w = make(ON, pages=pages)
    item = w.release("shot.jpg")
    assert w.web.fetched == [PAGE_1, PAGE_2]
    assert w.plist()[item].status == "done"


def test_when_every_candidate_fails_the_item_fails_like_any_unfetchable_page(make):
    w = make(ON, pages={})
    item = w.release("shot.jpg")
    assert w.web.fetched == [PAGE_1, PAGE_2]
    row = w.plist()[item]
    assert row.status == "failed" and "could not be fetched" in (row.link or "")


def test_max_candidates_limits_the_pages_that_are_fetched(make):
    w = make({**ON, "max_candidates": 1}, pages={})
    w.release("shot.jpg")
    assert w.web.fetched == [PAGE_1]


def test_an_address_in_the_image_wins_over_a_search(make):
    w = make(ON)
    item = w.release("withurl.jpg")
    assert QUERY not in w.search.queries  # no search for the item that shows its address
    assert w.web.fetched[0] == "https://direct.example/tool"
    assert w.plist()[item].status == "done"


def test_a_note_without_a_link_is_searched_with_its_text(make):
    w = make(ON)
    item = w.release(TEXT_ENTRY)
    assert w.search.queries[0] == TEXT_ENTRY
    assert w.plist()[item].status == "done"


def test_no_result_fails_the_item_with_a_hint_not_silently(make):
    w = make(ON, results=[])
    item = w.release("shot.jpg")
    row = w.plist()[item]
    assert row.status == "failed" and "no source found by web search" in (row.link or "")
    assert w.web.fetched == []


def test_nothing_searchable_in_the_description_is_a_failure_without_a_search(make):
    w = make(ON)
    item = w.release("blank.jpg")
    assert w.search.queries == [] and "nothing searchable" in (w.plist()[item].link or "")


def test_a_blocked_term_refuses_the_query_before_it_leaves(make):
    w = make(ON, security={"query_guard": {"blocked_terms": ["vectorlite"]}})
    item = w.release("shot.jpg")
    assert [q for q in w.search.queries if "ectorlite" in q] == []
    assert "web query guard" in (w.plist()[item].link or "") and w.web.fetched == []


def test_without_the_switch_the_behaviour_is_the_old_failure_and_no_search(make):
    w = make(None)
    item = w.release("shot.jpg")
    assert w.search.queries == [] and w.web.fetched == []
    assert "no source URL in the note" in (w.plist()[item].link or "")


def test_a_category_that_is_not_listed_is_not_searched(make):
    w = make({"enabled": True, "categories": ["business"]})
    item = w.release("shot.jpg")
    assert w.search.queries == [] and "no source URL" in (w.plist()[item].link or "")


def test_the_query_and_the_description_text_reach_no_span_message_or_trace(make):
    w = make(ON)
    w.release("shot.jpg")
    dump = repr([(s.name, s.attributes, s.events) for s in w.tracer.spans])
    assert "Vectorlite" not in dump and "vector search" not in dump
    assert all("Vectorlite" not in json.dumps(m, default=str) for m in w.notify.sent)


def test_an_image_without_a_cached_description_says_so(make):
    w = make(ON)
    assert w.run("triage")
    item = rid("shot.jpg")
    w.store.cache.clear()
    lists.owner_decide(w.triage_path, item, override="tool")
    assert w.svc.start("process_released").ok
    assert "no cached description" in (w.plist()[item].link or "")
