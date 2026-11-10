"""Spec 031: which items are unfinished, the label the triage shows, the table cells and the fallback."""

from __future__ import annotations

import pytest

from pkpipe.domain import in_progress as ip


@pytest.mark.parametrize(
    "state, status, held, expected",
    [
        ("processed", "failed", False, True),
        ("processed", "unprocessed", False, True),
        ("processed", "done", False, False),
        ("processed", "ready", False, False),
        ("processed", "done", True, True),  # a hold is open whatever the line says
        ("quarantined", "", False, True),
        ("released", "unprocessed", False, True),  # queued for a retry: the row stays
        ("pending", "", False, False),
        ("suggested", "", False, False),
        ("awaiting_category", "", False, False),
    ],
)
def test_unfinished_means_worked_on_and_not_closed(state, status, held, expected):
    assert ip.is_unfinished(state, status, held=held) is expected


def test_only_a_processed_item_with_done_or_ready_is_closed():
    assert ip.is_closed("processed", "done") and ip.is_closed("processed", "ready")
    assert not ip.is_closed("processed", "failed") and not ip.is_closed("quarantined", "done")


@pytest.mark.parametrize(
    "state, status, held, failed, code",
    [
        ("processed", "done", False, False, "done"),
        ("processed", "ready", False, False, "ready"),
        ("processed", "failed", False, True, "failed"),
        ("processed", "failed", True, True, "held"),
        ("processed", "unprocessed", False, False, "unprocessed"),
        ("processed", "", False, False, "processed"),
        ("quarantined", "", False, False, "quarantined"),
        ("pending", "", False, False, "pending"),
    ],
)
def test_the_triage_outcome_says_how_a_processed_item_went(state, status, held, failed, code):
    got, label = ip.outcome(state, status, held=held, failed=failed)
    assert got == code and label
    assert label != "processed" or code == "processed"  # 'processed' alone is never shown as a result


@pytest.mark.parametrize(
    "state, status, reason, held, code",
    [
        (
            "processed",
            "failed",
            "held for your decision: only the injection detector fired",
            True,
            "held_classifier",
        ),
        ("quarantined", "", "", False, "quarantined"),
        ("processed", "unprocessed", "no source found by web search; add a link", False, "no_source_found"),
        (
            "processed",
            "unprocessed",
            "no source found: the image has no cached description",
            False,
            "no_description",
        ),
        ("processed", "unprocessed", "the page could not be fetched", False, "page_not_fetched"),
        ("processed", "failed", "session 'extract' failed: ModelUnavailable", False, "model_unavailable"),
        ("processed", "failed", "something else failed", False, "session_failed"),
        ("processed", "unprocessed", "", False, "unfinished"),
    ],
)
def test_the_cause_code_comes_from_the_fixed_reason(state, status, reason, held, code):
    assert ip.cause_code(state, status, reason, held=held) == code


def test_a_cell_never_breaks_the_table_or_runs_markup():
    cell = ip.cell("a | b\n`c` <script> *x* [l](u)", 200)
    assert "\n" not in cell and "|" not in cell.replace("\\|", "") and "<script>" not in cell
    assert ip.uncell(cell) == "a | b 'c' <script> *x* [l](u)"
    assert len(ip.cell("x" * 500, 50)) <= 50


def test_the_entry_cell_is_exact_for_any_raw_text():
    raw = "- Title | with `tick` and \\\\ slash\nhttps://a.b/c_d?x=1\x1e- second"
    assert ip.decode_entry(ip.encode_entry(raw)) == raw
    assert ip.encode_entry("![[x.jpg]]").startswith("`")  # a code span: the embed does not render


def test_the_table_round_trips_and_ignores_what_it_did_not_write():
    row = {
        "item_id": "abc123456789",
        "kind": "link",
        "resource": "https://a.b/c_d?x=1|2",
        "category": "tool",
        "state": "processed",
        "situation": "failed | twice",
        "cause": "no_source_found",
        "next_step": "add a link",
        "since": "2026-11-10",
        "last_run": "r1",
        "entry": "- https://a.b/c_d?x=1|2",
        "facts": "f1",
    }
    text = ip.render_table([row], ui_url="http://ui:8787/") + "| not | a | row |\nfree text\n"
    (back,) = ip.parse_table(text)
    assert back["item_id"] == row["item_id"] and back["entry"] == row["entry"]
    assert back["situation"] == "failed | twice" and back["resource"] == row["resource"]
    assert "(http://ui:8787/items/abc123456789)" in back["history"]
    assert text.startswith("# INBOX in progress") and "Machine-owned" in text


def test_the_facts_hash_changes_with_the_facts_only():
    a = {"cause": "x", "events": ["e1"], "owner_retries": 0}
    assert ip.facts_hash(a) == ip.facts_hash(dict(reversed(list(a.items()))))
    assert ip.facts_hash(a) != ip.facts_hash({**a, "owner_retries": 1})


def test_the_facts_text_has_only_what_it_is_given_and_skips_empty_values():
    text = ip.facts_text({"kind": "link", "events": ["a", "b"], "held_review": None, "failure_reason": ""})
    assert "kind: link" in text and "  - a" in text and "held_review" not in text


def test_the_fallback_is_built_from_the_codes_and_the_answer_is_cut_to_its_limits():
    facts = {
        "cause": "no_source_found",
        "triage_state": "processed",
        "processing_status": "unprocessed",
        "failure_reason": "no source found by web search; add a link",
        "owner_retries": 2,
    }
    fb = ip.fallback_row(facts)
    assert "retried 2x" in fb["situation"] and fb["cause"] == "no_source_found" and "link" in fb["next_step"]
    cut = ip.clean_answer({"situation": "s" * 999, "cause": 5, "next_step": ""}, facts)
    assert len(cut["situation"]) == ip.MAX_SITUATION and cut["cause"] == fb["cause"]
    assert cut["next_step"] == fb["next_step"]
    assert ip.clean_answer("not json", facts) == fb
