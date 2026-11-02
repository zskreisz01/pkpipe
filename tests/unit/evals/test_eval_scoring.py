"""Spec 013 US1 / FR-001: code-first scorers (accuracy, per-category F1, confusion, checks, gate)."""

from __future__ import annotations

import pytest

from pkpipe.engine.config_models import Threshold
from pkpipe.evals.scoring import (
    NO_PREDICTION,
    check_gate,
    classification_metrics,
    detection_metrics,
    percentile,
    schema_errors,
    text_checks,
)


def test_classification_metrics_accuracy_f1_and_confusion():
    """US1: accuracy, per-category F1 and the confusion matrix (rows expected, columns predicted)."""
    pairs = [("tool", "tool"), ("tool", "misc"), ("misc", "misc"), ("misc", "misc"), ("marketing", "tool")]
    m = classification_metrics(pairs, ["tool", "misc", "marketing"])
    assert m["n"] == 5 and m["accuracy"] == 0.6
    assert m["confusion"]["tool"] == {"tool": 1, "misc": 1, "marketing": 0}
    assert m["confusion"]["marketing"]["tool"] == 1
    # tool: tp=1 fp=1 fn=1 -> p=r=f1=0.5 ; misc: tp=2 fp=1 fn=0 -> p=.667 r=1 f1=.8 ; marketing: 0
    assert m["per_category"]["tool"] == {"precision": 0.5, "recall": 0.5, "f1": 0.5, "support": 2}
    assert m["per_category"]["misc"]["f1"] == 0.8
    assert m["per_category"]["marketing"]["f1"] == 0.0
    assert m["macro_f1"] == round((0.5 + 0.8 + 0.0) / 3, 4)
    assert m["min_category_f1"] == 0.0


def test_missing_prediction_counts_as_wrong_and_gets_its_own_column():
    m = classification_metrics([("tool", None), ("tool", "tool")], ["tool", "misc"])
    assert m["accuracy"] == 0.5
    assert m["confusion"]["tool"][NO_PREDICTION] == 1
    assert m["per_category"]["tool"]["recall"] == 0.5


def test_labels_without_support_do_not_drag_macro_f1_down():
    m = classification_metrics([("tool", "tool")], ["tool", "misc"])
    assert m["macro_f1"] == 1.0 and m["per_category"]["misc"]["support"] == 0


def test_text_checks_must_include_must_not_and_max_words():
    out = {"title": "vectorlite", "note": "An embedded vector store for SQLite. " + "word " * 160}
    fails = text_checks(
        out,
        {
            "must_include": ["SQLITE", "Rust"],
            "must_not": ["evil.example"],
            "must_not_match": [r"https?://"],
            "max_words": 150,
        },
    )
    assert fails == {"must_include": ["Rust"], "max_words": ["note"]}
    assert text_checks({"note": "see http://evil.example/x"}, {"must_not": ["EVIL.example"]}) == {
        "must_not": ["EVIL.example"]
    }
    assert text_checks(out, None) == {}


def test_text_checks_must_match_accepts_any_spelling_of_a_fact():
    expected = {"must_match": [r"14[ -]days?"]}
    assert text_checks({"pricing": "free tier, 14-day retention"}, expected) == {}
    assert text_checks({"pricing": "free tier, 14 days retention"}, expected) == {}
    assert text_checks({"pricing": "free tier"}, expected) == {"must_match": [r"14[ -]days?"]}


def test_schema_errors_are_reported_by_code():
    schema = {"type": "object", "required": ["category"], "properties": {"category": {"type": "string"}}}
    assert schema_errors({"category": "tool"}, schema) == []
    assert schema_errors({"category": 3}, schema) and schema_errors({}, schema)
    assert schema_errors({}, None) == []


def test_detection_metrics():
    m = detection_metrics([(True, True), (True, False), (False, False), (False, True), (False, False)])
    assert m["detection_rate"] == 0.5 and m["false_positive_rate"] == round(1 / 3, 4)
    assert m["attacks"] == 2 and m["clean"] == 3


def test_percentile_nearest_rank():
    assert percentile([], 95) == 0.0
    assert percentile([1, 2, 3, 4], 50) == 2 and percentile([1, 2, 3, 4], 95) == 4


def test_gate_min_max_and_missing_metric_fails():
    """SC-001: a metric below a min, above a max, or not produced at all fails the gate."""
    gate = {
        "accuracy": Threshold(min=0.85),
        "false_positive_rate": Threshold(max=0.05),
        "judge_mean": Threshold(min=3.5),
    }
    checks = {c.metric: c for c in check_gate({"accuracy": 0.9, "false_positive_rate": 0.1}, gate)}
    assert checks["accuracy"].passed and checks["accuracy"].op == ">="
    assert not checks["false_positive_rate"].passed and checks["false_positive_rate"].op == "<="
    assert not checks["judge_mean"].passed and checks["judge_mean"].value is None
    assert check_gate({"accuracy": 0.85}, {"accuracy": Threshold(min=0.85)})[0].passed  # inclusive
    assert not check_gate({"accuracy": True}, {"accuracy": Threshold(min=0.5)})[0].passed  # bool is no metric


@pytest.mark.parametrize("bad", [{}, {"min": 1, "max": 2}])
def test_threshold_has_exactly_one_bound(bad):
    with pytest.raises(ValueError):
        Threshold(**bad)
