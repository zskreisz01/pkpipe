"""Builds the ClassifierPort named by ``security.classifier`` (spec 008 FR-004, spec 009 FR-002).

``backend``: ``qwen3guard`` (Qwen3Guard-Gen, harm categories and jailbreaks), ``deberta`` (ProtectAI
``deberta-v3-base-prompt-injection-v2``, prompt injection) or ``combined`` (its ``members``, maximum score).
Every backend loads lazily on the first scan, so building one never touches the model files.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pkpipe.adapters.classifier_combined import CombinedClassifier
from pkpipe.adapters.classifier_deberta import DebertaInjectionClassifier
from pkpipe.adapters.classifier_qwen3guard import Qwen3GuardClassifier
from pkpipe.ports import ClassifierPort

BACKENDS = ("qwen3guard", "deberta", "combined")


def build_classifier(classifier: Mapping[str, Any] | None, environ=None) -> ClassifierPort:
    cfg = dict(classifier or {})
    backend = cfg.get("backend") or "qwen3guard"
    if backend == "qwen3guard":
        return Qwen3GuardClassifier.from_config(cfg, environ)
    if backend == "deberta":
        return DebertaInjectionClassifier.from_config(cfg, environ)
    if backend == "combined":
        return CombinedClassifier([build_classifier(m, environ) for m in cfg.get("members") or []])
    raise ValueError(f"unknown classifier backend '{backend}' (known: {', '.join(BACKENDS)})")


def describe(classifier: Any) -> str:
    """Where the model files are expected (for notes and error messages)."""
    members = getattr(classifier, "members", None)
    if members:
        return ", ".join(describe(m) for m in members)
    return f"{getattr(classifier, 'model', '?')} at {getattr(classifier, 'directory', '?')}"


def is_available(classifier: Any) -> bool:
    """Whether a classifier can scan: its ``available()`` method (or flag); True when it has neither."""
    flag = getattr(classifier, "available", True)
    return bool(flag()) if callable(flag) else bool(flag)
