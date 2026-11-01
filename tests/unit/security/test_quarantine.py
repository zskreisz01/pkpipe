"""Spec 009 US2 (data-model): the malicious URL entry, the deny-list edit (one entry per domain, counter on
repeats) and the inert quarantine format that a release restores exactly."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pkpipe.evals.models import EvalCase
from pkpipe.security.quarantine import (
    MaliciousUrlEntry,
    add_to_deny_list,
    fence_for,
    flag_domain,
    quarantine_body,
    release_candidate,
    unwrap_inert,
    wrap_inert,
)
from pkpipe.security.urlfilter import parse_deny_list


@pytest.mark.parametrize(
    ("url", "domain"),
    [
        ("https://www.Evil.Example/x?y=1", "evil.example"),
        ("http://sub.evil.example:8080/", "sub.evil.example"),
        ("javascript:alert(1)", None),
        ("0-INBOX/note.md", None),
        ("", None),
        (None, None),
    ],
)
def test_flag_domain(url, domain):
    assert flag_domain(url) == domain


def test_deny_list_adds_a_domain_once_and_counts_repeats():
    """Spec 009 edge case: same domain flagged repeatedly -> single entry, counter incremented."""
    seed = "# pack list\ndenied.example\n"
    text, added, count = add_to_deny_list(seed, "evil.example", "2026-11-01")
    assert added and count == 1 and "evil.example  # pkpipe: flagged 1, last 2026-11-01" in text
    text, added, count = add_to_deny_list(text, "evil.example", "2026-11-02")
    assert not added and count == 2
    assert text.count("evil.example") == 1 and "flagged 2, last 2026-11-02" in text
    assert parse_deny_list(text) == {"denied.example", "evil.example"}
    assert text.startswith("# pack list\n")  # the file's other lines are kept


def test_deny_list_counts_a_subdomain_on_its_parent_entry_and_keeps_manual_comments():
    text, added, count = add_to_deny_list("bad.example  # added by hand\n", "cdn.bad.example", "2026-11-01")
    assert not added and count == 1
    assert text.strip() == "bad.example  # added by hand  # pkpipe: flagged 1, last 2026-11-01"
    text, _, count = add_to_deny_list(text, "bad.example", "2026-11-03")
    assert count == 2 and "# added by hand" in text and text.count("pkpipe:") == 1


def test_malicious_url_entry_validates_and_truncates_evidence():
    row = MaliciousUrlEntry(
        url="https://evil.example/x",
        domain="evil.example",
        flag="canary_leak",
        detector="canary",
        evidence="canary_leak",
        run_id="r",
        timestamp=datetime(2026, 11, 1, tzinfo=UTC),
    )
    assert row.model_dump(mode="json")["timestamp"].startswith("2026-11-01")
    with pytest.raises(ValidationError):
        MaliciousUrlEntry(url="u", flag="f", detector="d", run_id="r", timestamp="2026", evidence="x" * 201)


@pytest.mark.parametrize(
    "text",
    [
        "plain note\n",
        "---\nsource: https://x\n---\nbody with ``` fence and ```` longer one\n",
        "no trailing newline",
        "",
        "[link](http://x) ![[embed]] <script>x</script>\n",
    ],
)
def test_inert_wrap_round_trips_exactly(text):
    block = wrap_inert(text)
    assert block.startswith(fence_for(text) + "text\n")
    restored = unwrap_inert(quarantine_body("abc", text))
    assert restored == (text if text.endswith("\n") or not text else text + "\n")


def test_fence_is_longer_than_any_backtick_run():
    assert fence_for("a ````` b") == "``````" and fence_for("none") == "```"


def test_release_candidate_is_a_valid_security_eval_case():
    case = release_candidate(item_id="abc", text="x" * 5000, flags=["heuristic"], day="2026-11-01")
    parsed = EvalCase.model_validate({**case, "suite": "security"})
    assert parsed.source.value == "release" and parsed.expected["label"] == "benign"
    assert len(parsed.input["text"]) == 4000 and parsed.id == "release-abc-2026-11-01"
