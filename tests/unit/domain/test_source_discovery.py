"""Spec 025: the search query and the candidates (pure functions)."""

from __future__ import annotations

import pytest

from pkpipe.domain import source_discovery as sd
from pkpipe.engine.config_models import SourceDiscoveryConfig

T = "{topic} {text}"


def test_the_query_is_the_template_filled_with_plain_words():
    q = sd.build_query(T, "A screenshot of the Vectorlite tool.", "vector search for SQLite", 120)
    assert q == "A screenshot of the Vectorlite tool vector search for SQLite"  # edge punctuation is trimmed


def test_urls_markup_and_invisible_characters_never_reach_the_query():
    q = sd.build_query(
        T,
        "see https://evil.example/x?secret=1 and ![](https://e.example/a?d=1) [[wiki]] <b>bold</b>",
        "zero​width `code` and www.hidden.example/path",
        200,
    )
    for bad in ("http", "evil", "secret", "![", "[[", "<", "`", "hidden", "​", "(url)"):
        assert bad not in q
    assert "bold" in q and "code" in q


def test_the_query_is_cut_after_a_whole_word_and_never_longer_than_the_limit():
    q = sd.build_query(T, "alpha beta gamma delta", "epsilon zeta", 22)
    assert q == "alpha beta gamma delta" and len(q) <= 22
    q = sd.build_query(T, "alpha beta gamma delta", "epsilon zeta", 21)
    assert q == "alpha beta gamma" and len(q) <= 21
    assert len(sd.build_query(T, "", "x" * 1000, 50)) <= 50


def test_nothing_searchable_is_an_empty_query():
    assert sd.build_query(T, "", "", 100) == ""
    assert sd.build_query(T, "https://only.example/url", "!!! --- ???", 100) == ""


def _allowed(bad=()):
    return lambda url: not any(b in url for b in bad)


def test_candidates_are_http_urls_once_per_host_in_result_order_within_the_policy():
    results = {
        "results": [
            {"url": "ftp://x.example/a"},
            {"url": "https://user:pw@c.example/a"},
            {"url": "https://a.example/one"},
            {"url": "https://a.example/two"},  # a second page of the same host
            {"url": "https://blocked.example/x"},
            {"url": "https://b.example/page#frag"},
            {"url": "https://b.example/page"},
            {"url": 5},
            "junk",
            {"url": "https://d.example/last"},
        ]
    }
    urls, tally = sd.pick_candidates(results, _allowed(("blocked",)), 2)
    assert urls == ["https://a.example/one", "https://b.example/page#frag"]
    assert tally == {"results": 10, "dropped": 8}


@pytest.mark.parametrize("raw", [None, {}, {"results": None}, {"results": "x"}, [], "text"])
def test_a_malformed_search_result_is_no_candidate(raw):
    assert sd.pick_candidates(raw, _allowed(), 3)[0] == []


def test_the_failure_reasons_are_fixed_texts():
    for code in ("no_description", "nothing_to_search", "query_blocked", "no_results", "search_failed"):
        assert sd.failure_reason(code).startswith("no source found")
    assert "query" not in sd.failure_reason("no_results").split(":")[0]


def test_discovery_fields_keep_the_rest_for_later():
    assert sd.discovered_fields(["https://a/", "https://b/"]) == {
        "source_url": "https://a/",
        "source_candidates": ["https://b/"],
        "source_discovered": True,
    }


def test_the_config_is_off_by_default_and_validates_the_template():
    cfg = SourceDiscoveryConfig()
    assert cfg.enabled is False and cfg.categories == ["tool"] and cfg.max_candidates == 2
    for bad in ("{url}", "no placeholder", "{topic} {other}", "{0}"):
        with pytest.raises(ValueError):
            SourceDiscoveryConfig(query_template=bad)
    with pytest.raises(ValueError):
        SourceDiscoveryConfig(categories=["Not A Slug"])
    with pytest.raises(ValueError):
        SourceDiscoveryConfig(max_candidates=0)
    with pytest.raises(ValueError):
        SourceDiscoveryConfig(unknown_key=True)
    assert SourceDiscoveryConfig(categories=["tool", "tool", "business"]).categories == ["tool", "business"]
