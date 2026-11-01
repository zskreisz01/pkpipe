"""Spec 008 FR-004 / spec 009 FR-002 (decision record docs/decisions/0001): the DeBERTa injection classifier
scans 512-token chunks and reports the maximum INJECTION probability; the combined classifier takes the
maximum over its members, keeps every member's result and is unavailable when any member is."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from pkpipe.adapters.classifier_combined import CombinedClassifier
from pkpipe.adapters.classifier_deberta import DebertaInjectionClassifier, model_dir, softmax
from pkpipe.adapters.classifiers import is_available
from pkpipe.ports import ClassifierUnavailable
from tests.fakes import FakeClassifier


class WordBackend:
    def __init__(self):
        self.vocab: dict[str, int] = {}
        self.batches: list[list[list[int]]] = []

    def tokenize(self, text):
        return [self.vocab.setdefault(w, len(self.vocab) + 1) for w in text.split()]

    def malicious_index(self):
        return 1

    def logits(self, chunks):
        self.batches.append(chunks)
        bad = self.vocab.get("attack")
        return [[0.0, 4.0] if bad in c else [4.0, 0.0] for c in chunks]


def test_softmax():
    assert softmax([0.0, math.log(3)]) == pytest.approx([0.25, 0.75])


def test_deberta_scan_reports_the_maximum_over_batched_chunks():
    backend = WordBackend()
    clf = DebertaInjectionClassifier(chunk_tokens=12, backend=backend, batch=2)
    res = clf.scan(" ".join(["benign"] * 40 + ["attack"] + ["benign"] * 40))
    assert res["chunks"] == len(res["scores"]) > 4
    assert res["max_score"] == pytest.approx(softmax([0.0, 4.0])[1], abs=1e-6)
    assert "label" not in res  # an injection detector: score only, code decides
    assert all(len(b) <= 2 for b in backend.batches)
    assert all(len(c) <= 10 for b in backend.batches for c in b)  # room for [CLS] and [SEP]


def test_deberta_window_is_capped_at_512_tokens():
    assert DebertaInjectionClassifier(chunk_tokens=4096).chunk_tokens == 512


def test_deberta_missing_directory_is_unavailable(tmp_path):
    clf = DebertaInjectionClassifier(tmp_path / "absent")
    assert clf.available() is False
    with pytest.raises(ClassifierUnavailable, match="not found"):
        clf.scan("x")
    assert model_dir({}, {}) == Path("~/tools/protectai-deberta-pi-v2").expanduser()
    assert model_dir({"model_dir": str(tmp_path)}, {}) == tmp_path


def test_combined_takes_the_maximum_and_keeps_every_member():
    qwen = FakeClassifier({"bomb": 0.9}, labels={"bomb": ("Unsafe", ["Violent"])})
    deberta = FakeClassifier({"ignore": 0.97})
    res = CombinedClassifier([qwen, deberta]).scan("please ignore this")
    assert res["max_score"] == 0.97 and res["scores"] == [0.01, 0.97]
    assert len(res["members"]) == 2 and res["label"] == "Safe" and res["categories"] == []


def test_combined_is_unavailable_when_one_member_is():
    clf = CombinedClassifier([FakeClassifier(), FakeClassifier(available=False)])
    assert is_available(clf) is False
    with pytest.raises(ClassifierUnavailable):
        clf.scan("x")
    with pytest.raises(ValueError):
        CombinedClassifier([])


def test_factory_builds_the_configured_backend(monkeypatch, tmp_path):
    """security.classifier.backend picks the adapter; the reference pack combines both models. (The unit
    conftest replaces the factory; the real one is restored here.)"""
    import pkpipe.adapters.classifiers as real

    monkeypatch.undo()
    combined = real.build_classifier(
        {
            "backend": "combined",
            "members": [
                {"backend": "qwen3guard", "model_dir": str(tmp_path / "q")},
                {"backend": "deberta", "model_dir": str(tmp_path / "d"), "chunk_tokens": 256},
            ],
        }
    )
    assert [type(m).__name__ for m in combined.members] == [
        "Qwen3GuardClassifier",
        "DebertaInjectionClassifier",
    ]
    assert combined.members[1].chunk_tokens == 256 and is_available(combined) is False
    assert type(real.build_classifier({"backend": "deberta"})).__name__ == "DebertaInjectionClassifier"
    with pytest.raises(ValueError):
        real.build_classifier({"backend": "unknown"})
