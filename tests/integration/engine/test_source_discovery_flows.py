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


ON = {"enabled": True, "categories": ["tool"], "from_images": True}


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


def test_no_result_gives_the_image_its_own_note_instead_of_a_failure(make):
    """Spec 032: the search found nothing; the validated description becomes the item's result note."""
    w = make(ON, results=[])
    item = w.release("shot.jpg")
    row = w.plist()[item]
    assert row.status == "done" and w.web.fetched == []
    (note,) = w.notes_of(item)
    text = note.read_text(encoding="utf-8")
    assert "origin: image_description" in text and "Vectorlite" in text and "vector search for SQLite" in text


def test_nothing_searchable_in_the_description_is_a_failure_without_a_search(make):
    w = make(ON)
    item = w.release("blank.jpg")
    assert w.search.queries == [] and "nothing searchable" in (w.plist()[item].link or "")


def test_a_blocked_term_refuses_the_query_before_it_leaves(make):
    w = make(ON, security={"query_guard": {"blocked_terms": ["vectorlite"]}})
    item = w.release("shot.jpg")
    assert [q for q in w.search.queries if "ectorlite" in q] == []
    assert w.web.fetched == [] and w.plist()[item].status == "done"  # spec 032: the local note is no query


def test_without_the_switch_the_image_gets_its_own_note_and_no_search(make):
    w = make(None)
    item = w.release("shot.jpg")
    assert w.search.queries == [] and w.web.fetched == []
    assert w.plist()[item].status == "done" and len(w.notes_of(item)) == 1


def test_a_category_that_is_not_listed_is_not_searched(make):
    w = make({"enabled": True, "categories": ["business"], "from_images": True})
    item = w.release("shot.jpg")
    assert w.search.queries == [] and w.plist()[item].status == "done" and len(w.notes_of(item)) == 1


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


def test_a_description_made_with_an_older_prompt_is_still_found_at_release(make):
    """Spec 028: the release makes no model call, so a changed prompt digest must not hide the description."""
    w = make(ON)
    assert w.run("triage")
    item = rid("shot.jpg")
    w.store.cache = {(sha, model, "oldprompt0000"): v for (sha, model, _), v in w.store.cache.items()}
    lists.owner_decide(w.triage_path, item, override="tool")
    assert w.svc.start("process_released").ok
    row = w.plist()[item]
    assert row.status == "done", row


# -- spec 029: a classifier warning of the injection detector alone is held, not quarantined -------------
def _two_models(injection_rules, labels=None):
    from pkpipe.adapters.classifier_combined import CombinedClassifier
    from tests.fakes import FakeClassifier

    return CombinedClassifier(
        [
            FakeClassifier(labels=labels or {"zzz-never": ("Unsafe", ["Jailbreak"])}),
            FakeClassifier(rules=injection_rules),
        ]
    )


def _review_world(make, classifier):
    from tests.fakes import FakeClassifierReview

    w = make(ON)
    w.ports.extra["classifier"] = classifier
    w.ports.extra["classifier_reviews"] = FakeClassifierReview()
    return w


def test_the_injection_detector_alone_holds_the_item_for_the_owner(make):
    w = _review_world(make, _two_models({"vectorlite adds": 0.99}))
    item = w.release("shot.jpg")
    row = w.plist()[item]
    assert row.status == "failed" and "held for your decision" in (row.link or "")
    held = w.ports.extra["classifier_reviews"].review_of(item)
    assert held and held["evidence"]["reading"] == "injection_detector_only" and not held["waived"]
    assert "quarantined" not in {
        r.get("state") for r in lists.read_triage(w.triage_path)["rows"] if r["item_id"] == item
    }
    assert w.ports.extra["security_log"].rows == []  # nothing is logged as malicious, no host is deny-listed


def test_both_models_firing_still_quarantines(make):
    clf = _two_models({"vectorlite adds": 0.99}, labels={"vectorlite adds": ("Unsafe", ["Jailbreak"])})
    w = _review_world(make, clf)
    item = w.release("shot.jpg")
    state = next(r["state"] for r in lists.read_triage(w.triage_path)["rows"] if r["item_id"] == item)
    assert state == "quarantined"
    assert w.ports.extra["classifier_reviews"].review_of(item) is None
    assert w.ports.extra["security_log"].rows[0]["detail"]["reading"] == "both_over_threshold"


def test_an_accepted_warning_lets_the_held_page_through(make):
    w = _review_world(make, _two_models({"vectorlite adds": 0.99}))
    assert w.run("triage")
    item = rid("shot.jpg")
    w.ports.extra["classifier_reviews"].hold(item, "", {})
    w.ports.extra["classifier_reviews"].waive(item)
    lists.owner_decide(w.triage_path, item, override="tool")
    assert w.svc.start("process_released").ok
    assert w.plist()[item].status == "done", w.plist()[item]


# -- spec 032: the image note is guarded like any text derived from external content -------------
def test_an_injection_in_the_image_description_quarantines_the_item_and_no_note_is_written(make):
    from tests.fakes import FakeClassifier

    w = make(ON, results=[])
    # the rule matches text that exists only in the rendered note, so the triage-time scan of the
    # description passes and it is the output guard of the note that flags it
    w.ports.extra["classifier"] = FakeClassifier(rules={"No page was found for this image": 0.99})
    item = w.release("shot.jpg")
    state = next(r["state"] for r in lists.read_triage(w.triage_path)["rows"] if r["item_id"] == item)
    assert state == "quarantined" and w.notes_of(item) == []


def test_an_image_with_an_unusable_description_still_fails_with_the_search_reason(make):
    w = make(ON)  # blank.jpg: no topic worth a note, nothing searchable
    item = w.release("blank.jpg")
    row = w.plist()[item]
    assert row.status == "failed" and "nothing searchable" in (row.link or "") and w.notes_of(item) == []


# -- spec 032: auto_pass lets the injection detector alone through, logged ---------------------
AUTO = {"classifier": {"review": {"enabled": True, "mode": "auto_pass"}}}


def _auto_world(make, classifier, security=AUTO):
    from tests.fakes import FakeClassifierReview, FakeItemEvents

    w = make(ON, security=security)
    w.ports.extra["classifier"] = classifier
    w.ports.extra["classifier_reviews"] = FakeClassifierReview()
    w.ports.extra["item_events"] = FakeItemEvents()
    return w


def test_auto_pass_lets_the_page_through_and_logs_the_evidence(make):
    w = _auto_world(make, _two_models({"vectorlite adds": 0.99}))
    item = w.release("shot.jpg")
    assert w.plist()[item].status == "done" and len(w.notes_of(item)) == 1
    assert w.ports.extra["classifier_reviews"].review_of(item) is None  # not held
    assert w.ports.extra["security_log"].rows == []  # nothing logged as malicious, no host deny-listed
    (event,) = [e for e in w.ports.extra["item_events"].events_of(item) if e["kind"] == "auto_passed"]
    assert event["detail"]["classifier"]["reading"] == "injection_detector_only"


def test_auto_pass_still_quarantines_when_the_safety_model_fires_too(make):
    clf = _two_models({"vectorlite adds": 0.99}, labels={"vectorlite adds": ("Unsafe", ["Jailbreak"])})
    w = _auto_world(make, clf)
    item = w.release("shot.jpg")
    state = next(r["state"] for r in lists.read_triage(w.triage_path)["rows"] if r["item_id"] == item)
    assert state == "quarantined"
    assert [e for e in w.ports.extra["item_events"].events_of(item) if e["kind"] == "auto_passed"] == []


def test_the_default_mode_still_holds_the_item(make):
    w = _auto_world(
        make, _two_models({"vectorlite adds": 0.99}), security={"classifier": {"review": {"enabled": True}}}
    )
    item = w.release("shot.jpg")
    assert w.plist()[item].status == "failed" and "held for your decision" in (w.plist()[item].link or "")
