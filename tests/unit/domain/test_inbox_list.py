"""Spec 020 US1 and US3: the list-style INBOX parser, item identity and entry removal (T001, T002, T009)."""

from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path

import pytest

from pkpipe.domain.inbox_list import (
    EntryKind,
    InboxListConfig,
    archive_text,
    list_items,
    normalize_url,
    parse_list,
    remove_entries,
)
from pkpipe.domain.triage import item_id_for

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "inbox"
LIST = (FIXTURES / "INBOX-list.md").read_text(encoding="utf-8")


def test_golden_fixture_yields_exactly_the_expected_items():
    """SC-001: header, comments and duplicates produce no extra items; every other shape is one item."""
    expected = json.loads((FIXTURES / "INBOX-list.expected.json").read_text(encoding="utf-8"))
    items = list_items(parse_list(LIST))
    got = [
        {
            "kind": i.kind.value,
            "resource": i.resource,
            "title": i.title,
            "line": i.line,
            "entries": len(i.raws),
        }
        for i in items
    ]
    assert got == expected


def test_title_with_url_on_the_next_line_is_one_text_link_item():
    (entry,) = parse_list("---\n- Some title\nhttps://a.b/x?utm_source=fb\n")
    assert entry.kind is EntryKind.TEXT_LINK
    assert (entry.resource, entry.title) == ("https://a.b/x", "Some title")
    assert entry.raw == "- Some title\nhttps://a.b/x?utm_source=fb"


def test_same_link_with_and_without_tracking_parameters_has_one_identity():
    a, b = parse_list("---\n- https://a.b/x?utm_source=fb&fbclid=1\n- https://a.b/x\n")
    assert a.item_id == b.item_id == item_id_for("https://a.b/x")
    (item,) = list_items([a, b])
    assert item.raws == [a.raw, b.raw]


def test_normalize_url_strips_only_tracking_and_keeps_the_rest():
    assert normalize_url("HTTPS://A.b/Path?x=1&utm_medium=z&y=%20a&mc_eid=9&gclid=3#frag") == (
        "https://a.b/Path?x=1&y=%20a#frag"
    )
    assert normalize_url("https://a.b/?utm_source=x") == "https://a.b/"
    assert normalize_url("not a url") == "not a url"
    assert normalize_url("https://a.b/?fbclid=1", strip_params=()) == "https://a.b/?fbclid=1"


def test_comments_are_kept_out_of_the_entries_even_across_lines():
    text = "---\n- one\n<!-- note -->\n- two <!-- inline -->\n<!--\n- hidden\n-->\n- three\n"
    assert [e.resource for e in parse_list(text)] == ["one", "two", "three"]
    cfg = InboxListConfig(comments_ignored=False)
    assert parse_list(text, cfg)[0].resource == "one <!-- note -->"


def test_header_ends_at_the_marker_or_at_the_first_bullet():
    with_marker = "---\ntags: [a]\n---\n# Title\n- a bullet in the intro is part of the header\n---\n- real\n"
    assert [e.resource for e in parse_list(with_marker)] == ["real"]
    no_marker = "# Title\nintro\n- first\n- second\n"
    assert [e.resource for e in parse_list(no_marker)] == ["first", "second"]
    custom = InboxListConfig(header_end=r"^## Entries\s*$", bullet_markers=("*",))
    assert [e.resource for e in parse_list("intro\n## Entries\n* a\n- b\n", custom)] == [
        "a - b"  # a line that is no bullet of this config continues the entry above
    ]


def test_unknown_shapes_become_text_never_dropped():
    entries = parse_list("---\n-\n- ![[note-without-image-extension]]\n- [[Some Note]]\n- ![[x.jpg|200]]\n")
    kinds = [(e.kind.value, e.resource) for e in entries]
    assert kinds == [
        ("text", "![[note-without-image-extension]]"),
        ("text", "[[Some Note]]"),
        ("image", "x.jpg"),
    ]


def test_inline_url_after_text_is_a_text_link():
    (e,) = parse_list("---\n- Read this: https://a.b/c.\n")
    assert (e.kind, e.resource, e.title) == (EntryKind.TEXT_LINK, "https://a.b/c", "Read this")


def test_config_rejects_unknown_settings():
    with pytest.raises(ValueError, match="unknown inbox list setting"):
        InboxListConfig.from_mapping({"bullets": ["-"]})
    assert InboxListConfig.from_mapping({"bullet_markers": ["+"]}).bullet_markers == ("+",)


# -- removal -----------------------------------------------------------------------------------------------
def test_remove_entries_keeps_every_other_byte_and_reports_not_found():
    entries = parse_list(LIST)
    target = entries[2]  # the two-line text + link entry
    result = remove_entries(LIST, [target.raw, "- never existed"])
    assert result.removed == [target.raw] and result.not_found == ["- never existed"]
    assert target.raw.splitlines()[0] not in result.text
    lines = LIST.splitlines(keepends=True)
    kept = lines[:12] + lines[14:]  # entry occupies lines 13-14 (index 12-13)
    assert result.text == "".join(kept)


def test_entries_added_meanwhile_are_kept_and_matching_ignores_line_numbers():
    entry = parse_list(LIST)[1]
    changed = LIST.replace(
        "---\n- https://example.org/articles/alpha?",
        "---\n- brand new entry\n- https://example.org/articles/alpha?",
        1,
    )
    result = remove_entries(changed, [entry.raw])
    assert "- brand new entry\n" in result.text
    assert result.removed == [entry.raw]


def test_remove_matches_whole_entries_only():
    text = "---\n- Title\nhttps://a.b/x\n"
    result = remove_entries(text, ["- Title"])
    assert result.text == text and result.not_found == ["- Title"]


def test_duplicate_raws_are_removed_one_for_one():
    text = "---\n- a\n- b\n- a\n"
    assert remove_entries(text, ["- a"]).text == "---\n- b\n- a\n"
    assert remove_entries(text, ["- a", "- a"]).text == "---\n- b\n"
    assert remove_entries(text, ["- a", "- a", "- a"]).not_found == ["- a"]


def test_crlf_and_missing_final_newline_are_preserved():
    text = "---\r\n- a\r\n- b\r\n- c"
    result = remove_entries(text, ["- b"])
    assert result.text == "---\r\n- a\r\n- c"
    assert remove_entries(text, ["- c"]).text == "---\r\n- a\r\n- b\r\n"


def test_property_removal_never_changes_a_byte_outside_the_removed_entries():
    """SC-003: random lists and subsets; the result is the text minus exactly the removed lines."""
    rng = random.Random(20)
    pieces = [
        "- https://a.b/{n}?utm_source=x",
        "- ![[img{n}.jpg]]",
        "- Title {n}\nhttps://c.d/{n}",
        "- plain {n}",
        "<!-- comment {n} -->",
        "",
        "free text {n}",
    ]
    for round_ in range(200):
        body = [rng.choice(pieces).format(n=rng.randrange(6)) for _ in range(rng.randrange(1, 14))]
        eol = rng.choice(["\n", "\r\n"])
        text = "header\n---\n".replace("\n", eol) + eol.join(body) + rng.choice(["", eol])
        entries = parse_list(text)
        chosen = [e for e in entries if rng.random() < 0.5]
        result = remove_entries(text, [e.raw for e in chosen])
        dropped: set[int] = set()
        for taken in _expected_taken(entries, chosen):
            dropped.update(range(taken.line - 1, taken.line + taken.raw.count("\n")))
        want = "".join(ln for i, ln in enumerate(text.splitlines(keepends=True)) if i not in dropped)
        assert result.text == want, (round_, text)
        assert result.not_found == []
        # the remaining list parses to the entries that were not removed (as multiset of raw texts)
        left = sorted(e.raw for e in parse_list(result.text))
        assert left == sorted(e.raw for e in entries if e not in _expected_taken(entries, chosen)), round_


def _expected_taken(entries, chosen):
    """The entries remove_entries takes: for every chosen raw, the first not yet taken entry with that raw."""
    taken: list = []
    for c in chosen:
        hit = next(e for e in entries if e.raw == c.raw and e not in taken)
        taken.append(hit)
    return taken


def test_archive_text_appends_the_raw_entries_under_a_dated_header():
    first = archive_text(None, ["- a", "- b\nhttps://x"], date(2026, 11, 4))
    assert first == "# Inbox archive 2026-11-04\n\n- a\n- b\nhttps://x\n"
    assert archive_text(first, ["- c"], date(2026, 11, 4)) == first + "- c\n"
    assert archive_text("no newline", ["- c"], date(2026, 11, 4)) == "no newline\n- c\n"
