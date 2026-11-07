"""Spec 024: the pure rules of the run report: counts only, todo lines in the owner's format."""

from __future__ import annotations

import pytest

from pkpipe.domain import run_report as rr


def test_a_todo_line_is_date_todo_count_link():
    t = rr.Todo("approval", 14, "[triage UI](http://100.64.0.10:8787/triage)")
    assert t.line("2026-11-07") == (
        "- 2026-11-07 - Review triage decisions - 14 items - [triage UI](http://100.64.0.10:8787/triage)"
    )
    assert rr.Todo("tool_reads", 1).line("2026-11-07") == "- 2026-11-07 - Read tool evaluations - 1 item"


def test_zero_count_todos_are_omitted_and_the_order_is_fixed():
    todos = rr.build_todos(0, 17, 5, {"notebooks": "[[Manual-Resource-Processing-List]]"})
    assert [t.key for t in todos] == ["notebooks", "tool_reads"]
    assert rr.build_todos(0, 0, 0, {}) == []
    assert [t.key for t in rr.build_todos(3, 2, 1, {})] == ["approval", "notebooks", "tool_reads"]


def test_a_release_run_is_summarised_per_category_from_its_item_results():
    results = [
        {"status": "done", "item": {"category": "tool", "note": "3-Resources/Tools/a.md"}},
        {"status": "done", "item": {"category": "tool", "note": "3-Resources/Tools/b.md"}},
        {"status": "done", "item": {"category": "learning-material", "nb_id": "n1", "audio_path": "a.mp3"}},
        {"status": "done", "item": {"category": "learning-material", "nb_id": "n2"}},
        {
            "status": "done",
            "item": {"category": "learning-material", "nb_id": "n3", "processing_status": "failed"},
        },
        {"status": "done", "item": {"flagged": True}},
        {"status": "failed", "item": {}},
    ]
    s = rr.summarize_results(results)
    assert s["by_category"] == {"tool": 2, "learning-material": 3} and s["processed"] == 5
    assert (s["evaluations"], s["notebooks"], s["podcasts"], s["notebooks_failed"]) == (2, 2, 1, 1)
    assert (s["quarantined"], s["failed"]) == (1, 1)
    assert rr.summarize_results(None)["processed"] == 0


def test_new_suggestions_are_counted_per_suggested_category():
    rows = [
        {"ai_suggested_category": "tool"},
        {"ai_suggested_category": "tool"},
        {"ai_suggested_category": ""},
    ]
    assert rr.summarize_new_rows(rows) == {"tool": 2, "none": 1}


def _render(**kw):
    base = dict(
        kind="process_released",
        day="2026-11-07",
        clock="06:05",
        run_id="399cb7d7-1c7f",
        summary={"by_category": {"tool": 3, "misc": 1}, "processed": 4, "evaluations": 3, "notebooks": 0},
        awaiting=14,
        todos=rr.build_todos(14, 2, 0, {}),
    )
    base.update(kw)
    return rr.render_report(**base)


def test_the_release_report_has_the_sections_the_owner_asked_for():
    text = _render()
    assert text.splitlines()[0] == "2026-11-07 06:05 · run 399cb7d7"
    assert "**Processed (4):** tool 3, misc 1" in text
    assert "**Created:** 3 evaluations" in text
    assert "**Waiting for you:** 14 decision(s) in the triage" in text
    assert "**Todo**\n- 2026-11-07 - Review triage decisions - 14 items" in text
    assert "Process notebook materials and podcasts - 2 items" in text


def test_the_triage_report_lists_new_suggestions_and_images():
    text = _render(
        kind="triage",
        summary={"new_by_category": {"misc": 2, "tool": 1}, "images_described": 5, "images_deferred": 2},
    )
    assert "**New suggestions (3):** misc 2, tool 1" in text
    assert "images described 5 · images deferred 2 (first in line next run)" in text


def test_nothing_open_says_so():
    assert "**Todo:** nothing open" in _render(awaiting=0, todos=[])


def test_many_categories_are_cut_with_a_count():
    by = {f"cat-{i:02d}": 1 for i in range(12)}
    text = _render(summary={"by_category": by, "processed": 12})
    assert "+4 more" in text


def test_a_too_long_report_keeps_the_todo_block_whole():
    notes = ["very long note " * 80]
    text = _render(notes=notes, todos=rr.build_todos(14, 2, 5, {"tool_reads": "[[Tools/x]]"}), max_chars=600)
    assert len(text) <= 600
    assert "**Todo**" in text and "Read tool evaluations - 5 items - [[Tools/x]]" in text
    assert "Review triage decisions - 14 items" in text and "**Waiting for you:**" in text


@pytest.mark.parametrize(
    "path,expected",
    [
        ("3-Resources/Tools/fancy-tool-abc123.md", "[[3-Resources/Tools/fancy-tool-abc123]]"),
        ("1-Inbox/Manual-Resource-Processing-List.md", "[[1-Inbox/Manual-Resource-Processing-List]]"),
        ("Tools/A note (v2).md", "[[Tools/A note (v2)]]"),
        ("Tools/x]].md", None),
        ("Tools/a|b.md", None),
        ("Tools/<img src=x>.md", None),
        ("Tools/`code`.md", None),
        ("Tools/a#b.md", None),
        ("../outside.md", None),
        ("Tools/folder", None),
        (None, None),
        ("x" * 200 + ".md", None),
    ],
)
def test_a_wikilink_is_made_only_from_plain_text(path, expected):
    assert rr.wikilink(path) == expected


def test_external_links_are_only_the_owners_own_http_address():
    assert rr.external_link("triage UI", "http://100.64.0.10:8787/triage") == (
        "[triage UI](http://100.64.0.10:8787/triage)"
    )
    for bad in (None, "", "javascript:alert(1)", "http://x/ y", "http://x/)[evil](http://y", "http://x/<b>"):
        assert rr.external_link("l", bad) is None
