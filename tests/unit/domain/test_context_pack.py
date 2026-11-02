"""Spec 011 FR-001, FR-002, US2, SC-001: the pure logic of the context packs (anonymization, manual edits,
token budget, terms file). Names are invented."""

from __future__ import annotations

import pytest

from pkpipe.domain import context_pack as cp
from pkpipe.security import query_guard


def note(path, front_matter=None, body=""):
    return {"path": path, "front_matter": front_matter or {}, "body": body}


# -- the pack file and manual edits (FR-002) ----------------------------------------------------------------
def test_render_and_parse_roundtrip_with_manual_section():
    text = cp.render_pack("# Pack\n\n- one", "My own rule.")
    parsed = cp.parse_pack(text)
    assert parsed.body == "# Pack\n\n- one" and parsed.manual == "My own rule."
    assert parsed.header == cp.digest("# Pack\n\n- one") and not parsed.edited


def test_a_hand_written_file_counts_as_edited():
    assert cp.parse_pack("# my own pack\n\ntext").edited
    edited = cp.render_pack("# Pack\n\n- one").replace("- one", "- two")
    assert cp.parse_pack(edited).edited


def test_the_bookkeeping_comments_are_no_context_for_a_session():
    text = cp.render_pack("# Pack\n\n- one", "Manual line.")
    shown = cp.strip_markers(text)
    assert "pkpipe:" not in shown and "- one" in shown and "Manual line." in shown


def test_merge_creates_updates_and_leaves_an_unchanged_pack_alone():
    created = cp.merge_pack(None, "# Pack\n\n- one")
    assert created.status == "created" and created.text and "- one" in created.diff
    again = cp.merge_pack(created.text, "# Pack\n\n- one")
    assert again.status == "unchanged" and not again.changed and again.text is None
    updated = cp.merge_pack(created.text, "# Pack\n\n- two")
    assert updated.status == "updated" and "-- one" in updated.diff and "+- two" in updated.diff
    assert cp.parse_pack(updated.text or "").body == "# Pack\n\n- two"


def test_merge_never_overwrites_an_edited_pack_and_proposes_a_diff():
    """FR-002: the owner's edit is kept; the new version is a proposal with the diff."""
    written = cp.merge_pack(None, "# Pack\n\n- one").text
    mine = written.replace("- one", "- one (mine)")
    result = cp.merge_pack(mine, "# Pack\n\n- two")
    assert result.status == "proposed" and result.text is None
    assert "+- two" in result.diff and "-- one (mine)" in result.diff
    assert cp.parse_pack(result.proposal or "").body == "# Pack\n\n- two"


def test_the_manual_section_is_carried_into_updates_and_proposals():
    written = cp.merge_pack(None, "# Pack\n\n- one").text + f"\n{cp.MANUAL_MARKER}\nKeep SQLite.\n"
    assert cp.merge_pack(written, "# Pack\n\n- two").text.rstrip().endswith("Keep SQLite.")
    edited = written.replace("- one", "- changed")
    assert "Keep SQLite." in cp.merge_pack(edited, "# Pack\n\n- two").proposal


def test_an_empty_file_is_treated_as_missing():
    assert cp.merge_pack("  \n", "# Pack").status == "created"


# -- names (US2) --------------------------------------------------------------------------------------------
def test_harvest_takes_front_matter_wikilinks_labelled_lines_and_titles():
    notes = [
        note(
            "1-Projects/Enterprise/Project Falcon - plan.md",
            {"client": "Brightwater Bank", "people": ["Dana Okafor", "[[Lee Park]]"], "status": "active"},
            "Customer: Marlow Retail Group\nSponsor: Alan Reddick. Architects: Rui Matos and Sven Aaltonen.",
        ),
        note("1-Projects/Enterprise/Roadmap.md", {}, "Nothing named here."),
    ]
    names = {t.term: t.kind for t in cp.harvest_terms(notes)}
    assert names["Brightwater Bank"] == "client" and names["Marlow Retail Group"] == "client"
    assert names["Dana Okafor"] == "person" and names["Lee Park"] == "person"
    assert {"Alan Reddick", "Rui Matos", "Sven Aaltonen"} <= set(names)
    assert names["Project Falcon"] == "project"  # the part of the title before " - "
    assert "Roadmap" not in names and "active" not in names


@pytest.mark.parametrize("title", ["Roadmap", "Data platform notes", "2026-10 Meeting", "Architecture"])
def test_generic_titles_are_no_names(title):
    assert cp.generic_title(title)
    assert not cp.harvest_terms([note(f"x/{title}.md")])


def test_session_names_count_only_when_they_occur_in_the_notes():
    terms = cp.session_terms(["Brightwater", "Made Up Inc", "AI", "the", 7], "The Brightwater team")
    assert [t.term for t in terms] == ["Brightwater"]


def test_unique_terms_drops_generic_short_and_duplicate_terms():
    got = cp.unique_terms(
        [
            cp.Term("Brightwater Bank"),
            cp.Term("brightwater   bank"),
            cp.Term("Inc."),
            cp.Term("Project"),
            cp.Term("ab"),
        ]
    )
    assert [t.term for t in got] == ["Brightwater Bank"]


def test_variants_cover_the_short_forms_people_write():
    variants = set(cp.term_variants(cp.Term("Brightwater Bank", "client")))
    assert {"Brightwater Bank", "Brightwater"} <= variants
    assert "Falcon" in cp.term_variants(cp.Term("Project Falcon", "project"))
    assert "Acme" in cp.term_variants(cp.Term("Acme Corp", "client"))
    person = set(cp.term_variants(cp.Term("Dana Okafor", "person")))
    assert {"Dana Okafor", "Dana", "Okafor"} <= person


def test_scrub_is_case_accent_and_punctuation_insensitive_and_whole_word():
    terms = [cp.Term("Café Müller GmbH", "client"), cp.Term("Project Falcon", "project")]
    text, n = cp.scrub("CAFE MULLER's Project-Falcon (project falcon) vs Mullerian.", terms)
    assert "[client]" in text and "[project]" in text
    assert "Mullerian" in text  # a longer word is not a match
    assert n == 3 and not cp.leaks(text, terms)


def test_scrub_keeps_the_surrounding_text_and_positions():
    terms = [cp.Term("Brightwater Bank", "client")]
    text, _ = cp.scrub("Ledger for Brightwater Bank on Kubernetes. Brightwater likes it.", terms)
    assert text == "Ledger for [client] on Kubernetes. [client] likes it."


def test_leaks_uses_the_same_matching_as_the_query_guard():
    terms = [cp.Term("Project Falcon", "project")]
    for hit in ("project falcon", "Project-Falcon", "plan: PROJECT   FALCON!", "falcon"):
        assert cp.leaks(hit, terms) == (
            query_guard.check_arguments(hit, ["Project Falcon", "Falcon"]) is not None
        )
    assert cp.leaks("falcon", terms) and not cp.leaks("peregrine falconry", terms)


# -- the terms file -----------------------------------------------------------------------------------------
def test_terms_file_keeps_owner_lines_and_only_appends():
    current = "# header\nMy Client\n"
    text, added = cp.merge_terms_file(
        current, [cp.Term("my   client"), cp.Term("Project Falcon")], "2026-11-01"
    )
    assert added == ["Project Falcon"]
    assert text.startswith(current) and "# added by context_refresh on 2026-11-01\nProject Falcon\n" in text
    assert cp.merge_terms_file(text, [cp.Term("Project Falcon")], "2026-11-08") == (text, [])
    assert query_guard.parse_terms(text) == ["My Client", "Project Falcon"]


def test_terms_file_from_nothing_and_without_final_newline():
    text, added = cp.merge_terms_file(None, [cp.Term("Brightwater Bank")], "2026-11-01")
    assert added == ["Brightwater Bank"] and text.endswith("Brightwater Bank\n")
    text, _ = cp.merge_terms_file("Old", [cp.Term("New Name")], "2026-11-01")
    assert text.startswith("Old\n# added")


# -- rendering within the budget ----------------------------------------------------------------------------
SUMMARY = {
    "overview": "Payment programmes for Brightwater Bank on Kubernetes.",
    "projects": [
        {
            "name": "Ledger of Brightwater Bank",
            "summary": "Oracle to PostgreSQL. Dana Okafor owns it.",
            "stack": ["PostgreSQL"],
        },
        {
            "name": "Data platform",
            "summary": "Kafka and Spark.",
            "stack": ["Kafka", "Spark"],
            "constraints": ["GDPR"],
        },
    ],
}


def test_enterprise_body_is_scrubbed_and_verified():
    terms = [cp.Term("Brightwater Bank", "client"), cp.Term("Dana Okafor", "person")]
    r = cp.render_body("enterprise", SUMMARY, max_tokens=1500, terms=terms)
    assert not cp.leaks(r.body, terms) and r.scrubbed >= 3 and r.projects == 2 and r.dropped == 0
    assert "PostgreSQL" in r.body and "Constraints: GDPR." in r.body
    assert r.body.startswith("# Enterprise context (anonymized)")


def test_personal_body_keeps_names_and_has_no_constraints_column():
    r = cp.render_body("personal", SUMMARY, max_tokens=1500)
    assert "Brightwater Bank" in r.body and "Constraints" not in r.body
    assert r.body.startswith("# Personal context") and "## Projects" in r.body


def test_projects_that_do_not_fit_the_budget_are_left_out_whole():
    many = {
        "overview": "x",
        "projects": [{"name": f"P{i}", "summary": "word " * 50, "stack": []} for i in range(30)],
    }
    r = cp.render_body("personal", many, max_tokens=400)
    assert r.tokens <= 400 and 0 < r.projects < 30 and r.trimmed == 30 - r.projects
    assert all(line.rstrip().endswith("…") or "word" in line for line in r.body.splitlines()[-3:])


def test_the_empty_pack_says_so():
    r = cp.render_body("enterprise", None, max_tokens=100)
    assert "No project notes were found" in r.body and r.projects == 0


def test_clip_cuts_at_a_word_boundary():
    assert cp.clip("one two three four", 9) == "one two…"
    assert cp.clip("short", 50) == "short"


def test_review_note_explains_each_status():
    proposed = cp.review_note("personal", "proposed", "-a\n+b")
    assert "personal.md.proposed" in proposed and "```diff" in proposed and cp.MANUAL_MARKER in proposed
    assert "was updated" in cp.review_note("personal", "updated", "")
    assert "was created" in cp.review_note("personal", "created", "")
