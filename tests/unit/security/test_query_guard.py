"""Spec 010 FR-002: the query guard refuses web queries that carry a blocked (client or project) name."""

from __future__ import annotations

import pytest

from pkpipe.security import query_guard as qg

TERMS = ["Project Falcon", "ExampleCorp"]


@pytest.mark.parametrize(
    "query",
    [
        "project falcon vector database",
        "PROJECT   FALCON",
        "Project-Falcon roadmap",
        "tools used by examplecorp",
        "ExampleCorp's stack",  # possessive: still a whole word
        "proj​ect falcon",  # zero-width space inside the word is dropped
        "Prôject Fàlcon",  # accents are folded
    ],
)
def test_a_blocked_term_is_found_whatever_the_spelling(query):
    assert qg.check_arguments({"query": query}, TERMS) == qg.REASON


@pytest.mark.parametrize(
    "query", ["falcon heavy rocket", "example corporation history", "vector search sqlite", ""]
)
def test_other_queries_pass(query):
    assert qg.check_arguments({"query": query}, TERMS) is None


def test_every_string_argument_is_checked_including_nested_ones():
    assert qg.check_arguments({"query": "ok", "filters": {"site": ["examplecorp.com"]}}, TERMS) == qg.REASON
    assert qg.check_arguments({"max_results": 5, "query": "ok"}, TERMS) is None


def test_the_reason_never_contains_the_term():
    assert "falcon" not in qg.check_arguments({"query": "project falcon"}, TERMS).lower()


def test_terms_come_from_config_and_a_terms_file_without_comments_and_blanks():
    terms = qg.blocked_terms({"blocked_terms": ["Alpha"]}, "# clients\nBeta\n\n  Gamma Corp  \nAlpha\n")
    assert terms == ["Alpha", "Beta", "Gamma Corp"]
    assert qg.blocked_terms(None, None) == [] and qg.blocked_terms({"blocked_terms": [" ", "-"]}, None) == []
    assert qg.check_arguments({"query": "anything"}, ["", "  ", "-"]) is None  # empty terms block nothing
