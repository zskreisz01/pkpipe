"""Code-first scorers (docs/evaluation.md "Scoring rules"): whatever code can check is never left to an
LLM."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import jsonschema

from pkpipe.engine.config_models import Threshold
from pkpipe.evals.models import GateCheck

#: label used in the confusion matrix for a missing prediction (model error, invalid output)
NO_PREDICTION = "(none)"


def classification_metrics(pairs: Sequence[tuple[str, str | None]], labels: Iterable[str]) -> dict[str, Any]:
    """Accuracy, per-category precision / recall / F1, macro F1 and the confusion matrix.

    ``pairs`` are (expected, predicted); ``predicted=None`` (no valid answer) counts as wrong. The matrix is
    ``confusion[expected][predicted]``; every expected label has a row, predictions outside ``labels``
    get their own column.
    """
    labels = list(dict.fromkeys(labels))
    for exp, _ in pairs:
        if exp not in labels:
            labels.append(exp)
    columns = list(labels)
    for _, pred in pairs:
        p = pred if pred is not None else NO_PREDICTION
        if p not in columns:
            columns.append(p)
    confusion = {e: {c: 0 for c in columns} for e in labels}
    for exp, pred in pairs:
        confusion[exp][pred if pred is not None else NO_PREDICTION] += 1
    per: dict[str, dict[str, float | int]] = {}
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[e][label] for e in labels if e != label)
        fn = sum(n for c, n in confusion[label].items() if c != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": tp + fn,
        }
    supported = [label for label in labels if per[label]["support"]]
    n = len(pairs)
    correct = sum(1 for e, p in pairs if p == e)
    return {
        "n": n,
        "accuracy": round(correct / n, 4) if n else 0.0,
        "macro_f1": round(sum(per[label]["f1"] for label in supported) / len(supported), 4)
        if supported
        else 0.0,
        "min_category_f1": min((per[label]["f1"] for label in supported), default=0.0),
        "per_category": per,
        "confusion": confusion,
    }


def schema_errors(value: Any, schema: Mapping[str, Any] | None) -> list[str]:
    if schema is None:
        return []
    validator = jsonschema.Draft202012Validator(schema)
    return [e.message for e in validator.iter_errors(value)]


def _flatten_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return "\n".join(_flatten_text(v) for v in value.values())
    if isinstance(value, list | tuple):
        return "\n".join(_flatten_text(v) for v in value)
    return "" if value is None else str(value)


def text_checks(output: Any, expected: Mapping[str, Any] | None) -> dict[str, list[str]]:
    """``must_include`` / ``must_not`` (case-insensitive substrings), ``must_not_match`` (regex) and
    ``max_words`` (per string field) on every string in the output. Returns failures by check name."""
    expected = expected or {}
    text = _flatten_text(output)
    low = text.lower()
    out: dict[str, list[str]] = {}
    missing = [s for s in expected.get("must_include", []) if s.lower() not in low]
    if missing:
        out["must_include"] = missing
    present = [s for s in expected.get("must_not", []) if s.lower() in low]
    if present:
        out["must_not"] = present
    matched = [p for p in expected.get("must_not_match", []) if re.search(p, text, re.IGNORECASE)]
    if matched:
        out["must_not_match"] = matched
    limit = expected.get("max_words")
    if limit is not None and isinstance(output, Mapping):
        long = [k for k, v in output.items() if isinstance(v, str) and len(v.split()) > int(limit)]
        if long:
            out["max_words"] = long
    return out


def detection_metrics(results: Sequence[tuple[bool, bool]]) -> dict[str, Any]:
    """(is_attack, flagged) pairs -> detection rate (recall on attacks) and false positive rate."""
    attacks = [f for a, f in results if a]
    clean = [f for a, f in results if not a]
    return {
        "attacks": len(attacks),
        "clean": len(clean),
        "detected": sum(attacks),
        "false_positives": sum(clean),
        "detection_rate": round(sum(attacks) / len(attacks), 4) if attacks else 0.0,
        "false_positive_rate": round(sum(clean) / len(clean), 4) if clean else 0.0,
    }


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100); 0.0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return float(ordered[rank - 1])


def check_gate(metrics: Mapping[str, Any], gate: Mapping[str, Threshold]) -> list[GateCheck]:
    """Compare metrics to thresholds. A metric the suite did not produce fails its check."""
    checks = []
    for metric, t in gate.items():
        value = metrics.get(metric)
        number = float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
        if t.min is not None:
            checks.append(
                GateCheck(
                    metric=metric,
                    op=">=",
                    threshold=t.min,
                    value=number,
                    passed=number is not None and number >= t.min,
                )
            )
        else:
            checks.append(
                GateCheck(
                    metric=metric,
                    op="<=",
                    threshold=t.max,
                    value=number,
                    passed=number is not None and number <= t.max,
                )
            )
    return checks
