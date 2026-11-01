"""Spec 008 FR-006: the verdict is code: any of URL filter, heuristics, classifier, canary or the Reader's
``injection_suspected`` raises a security flag; a timeout or unreachable page is a failure, not a flag."""

from __future__ import annotations

import pytest

from pkpipe.security.verdict import ReaderExtract, Verdict, evaluate

CLEAN = {
    "url_check": {"allowed": True, "flag": False},
    "fetch": {"status": "ok", "fetcher": "lightpanda"},
    "heuristics": {"score": 0.0, "matches": []},
    "classifier": {"available": True, "max_score": 0.02},
    "reader": {"injection_suspected": False, "canary_leaked": False},
}


def _v(**over) -> Verdict:
    return evaluate(**{**CLEAN, **over})


def test_all_clear_is_clean():
    v = _v()
    assert v.status == "clean" and v.clean and v.flags == []
    assert v.scores == {"fetcher": "lightpanda", "fallback_used": False, "heuristic": 0.0, "classifier": 0.02}


@pytest.mark.parametrize(
    ("over", "flag", "reason"),
    [
        ({"url_check": {"allowed": False, "flag": True, "reason": "deny_list"}}, "url_filter", "deny_list"),
        ({"fetch": {"status": "blocked", "reason": "private_network"}}, "url_filter", "private_network"),
        ({"heuristics": {"score": 1.0, "matches": ["override"]}}, "heuristic", "heuristic:override"),
        ({"classifier": {"available": True, "max_score": 0.5}}, "classifier", "classifier_threshold"),
        ({"classifier": {"available": False}}, "classifier", "classifier_unavailable"),
        ({"reader": {"injection_suspected": False, "canary_leaked": True}}, "canary", "canary_leak"),
        ({"reader": {"injection_suspected": True, "canary_leaked": False}}, "reader", "injection_suspected"),
    ],
)
def test_each_detector_alone_raises_a_flag(over, flag, reason):
    v = _v(**over)
    assert v.status == "flagged" and not v.clean and v.flags == [flag]
    assert v.flag_reasons[flag] == reason


def test_several_detectors_are_all_listed():
    v = _v(
        heuristics={"score": 2.0, "matches": ["override", "role_tags"]},
        reader={"injection_suspected": True, "canary_leaked": True},
    )
    assert v.flags == ["heuristic", "canary", "reader"]


def test_thresholds_come_from_config():
    assert _v(heuristics={"score": 1.0, "matches": ["x"]}, heuristic_threshold=2.0).clean
    assert not _v(classifier={"available": True, "max_score": 0.3}, classifier_threshold=0.3).clean


def test_missing_classifier_passes_only_when_not_required():
    v = _v(classifier={"available": False}, classifier_required=False)
    assert v.clean and v.scores["classifier"] is None


@pytest.mark.parametrize(
    "over",
    [
        {"fetch": {"status": "failed", "reason": "timeout"}},
        {"url_check": {"allowed": False, "flag": False, "reason": "unresolvable"}},
    ],
)
def test_failures_are_not_security_flags(over):
    v = _v(**over, heuristics=None, classifier=None, reader=None)
    assert v.status == "failed" and not v.clean and v.flags == []


def test_a_flag_wins_over_a_failure():
    v = _v(url_check={"allowed": False, "flag": True, "reason": "private_network"}, fetch=None)
    assert v.status == "flagged"


def test_reader_extract_rejects_extra_fields():
    with pytest.raises(ValueError):
        ReaderExtract.model_validate(
            {"title": "t", "summary": "s", "facts": [], "injection_suspected": False, "tool": "x"}
        )
