"""Spec 029: the evidence of the classifier models, its reading, and the review rule of the verdict."""

from __future__ import annotations

import pytest

from pkpipe.security.evidence import (
    READING_BOTH,
    READING_INJECTION_ONLY,
    READING_SAFETY_ONLY,
    classifier_evidence,
    interpret,
    review_applies,
)
from pkpipe.security.verdict import evaluate

DEBERTA = {
    "model": "protectai/deberta-v3-base-prompt-injection-v2",
    "max_score": 0.99,
    "chunks": 4,
    "scores": [0.99, 0.98, 0.04, 0.0],
}
QWEN_SAFE = {
    "model": "Qwen/Qwen3Guard-Gen-0.6B",
    "max_score": 0.004,
    "chunks": 2,
    "scores": [0.004, 0.0],
    "label": "Safe",
    "categories": [],
}
QWEN_UNSAFE = {
    **QWEN_SAFE,
    "max_score": 0.9,
    "scores": [0.9, 0.0],
    "label": "Unsafe",
    "categories": ["Jailbreak"],
}


def combined(*members, top=None):
    return {
        "available": True,
        "max_score": top or max(m["max_score"] for m in members),
        "members": list(members),
    }


def test_evidence_names_the_models_chunks_and_reading():
    ev = classifier_evidence(combined(QWEN_SAFE, DEBERTA), 0.5)
    deb = next(m for m in ev["members"] if m["kind"] == "injection")
    assert deb["model"] == "deberta-v3-base-prompt-injection-v2" and deb["over"] is True
    assert deb["flagged_chunks"] == [1, 2] and deb["flagged_chunk_count"] == 2 and deb["chunks"] == 4
    saf = next(m for m in ev["members"] if m["kind"] == "safety")
    assert saf["label"] == "Safe" and saf["over"] is False and saf["flagged_chunks"] == []
    assert ev["reading"] == READING_INJECTION_ONLY and review_applies(ev)


def test_both_or_safety_only_is_a_real_finding():
    both = classifier_evidence(combined(QWEN_UNSAFE, DEBERTA), 0.5)
    assert both["reading"] == READING_BOTH and not review_applies(both)
    only_safety = classifier_evidence(
        combined(QWEN_UNSAFE, {**DEBERTA, "max_score": 0.1, "scores": [0.1]}), 0.5
    )
    assert only_safety["reading"] == READING_SAFETY_ONLY and not review_applies(only_safety)


def test_a_single_model_has_no_second_opinion():
    ev = classifier_evidence({"available": True, "max_score": 0.99, "scores": [0.99]}, 0.5)
    assert ev["reading"] == "single_model" and not review_applies(ev)
    ev2 = classifier_evidence(combined(DEBERTA), 0.5)  # a combination without a safety model
    assert not review_applies(ev2)


def test_the_sentences_explain_the_scores_and_hold_no_page_text():
    lines = interpret(classifier_evidence(combined(QWEN_SAFE, DEBERTA), 0.5))
    text = "\n".join(lines)
    assert "0.99" in text and "chunk(s) 1, 2 of 4" in text and "label Safe" in text
    assert "false positive" in text and "markup" in text


CLEAN = {
    "url_check": {"allowed": True, "flag": False},
    "fetch": {"status": "ok", "fetcher": "lightpanda"},
    "heuristics": {"score": 0.0, "matches": []},
    "reader": {"injection_suspected": False, "canary_leaked": False},
}


def verdict(classifier, **kw):
    return evaluate(**{**CLEAN, "classifier": classifier}, **kw)


def test_without_the_review_switch_the_flag_quarantines_as_before_and_carries_the_evidence():
    v = verdict(combined(QWEN_SAFE, DEBERTA))
    assert v.status == "flagged" and v.flags == ["classifier"]
    assert v.evidence["classifier"]["reading"] == READING_INJECTION_ONLY


def test_the_injection_detector_alone_is_held_when_the_review_is_on():
    v = verdict(combined(QWEN_SAFE, DEBERTA), review_enabled=True)
    assert v.status == "review" and v.review == "classifier_injection_only"
    assert not v.clean and v.flags == []


@pytest.mark.parametrize(
    "case",
    [
        {"classifier": combined(QWEN_UNSAFE, DEBERTA)},  # both models fire
        {"classifier": combined(QWEN_SAFE, DEBERTA), "heuristics": {"score": 2.0, "matches": ["override"]}},
        {"classifier": combined(QWEN_SAFE, DEBERTA), "reader": {"injection_suspected": True}},
        {"classifier": combined(QWEN_SAFE, DEBERTA), "reader": {"canary_leaked": True}},
        {"classifier": combined(QWEN_SAFE, DEBERTA), "reader": None},  # no Reader opinion
        {"classifier": combined(DEBERTA)},  # no safety model
    ],
)
def test_every_other_combination_keeps_the_quarantine(case):
    v = evaluate(**{**CLEAN, **case}, review_enabled=True)
    assert v.status == "flagged" and not v.clean


def test_an_accepted_warning_lets_the_page_pass_but_not_other_findings():
    ok = verdict(combined(QWEN_SAFE, DEBERTA), review_enabled=True, classifier_waived=True)
    assert ok.status == "clean" and ok.clean and ok.scores["classifier_waived"] is True
    bad = evaluate(
        **{**CLEAN, "classifier": combined(QWEN_SAFE, DEBERTA), "reader": {"injection_suspected": True}},
        review_enabled=True,
        classifier_waived=True,
    )
    assert bad.status == "flagged"  # the Reader still counts
