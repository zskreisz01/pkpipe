"""Spec 008 FR-004: the Prompt Guard 2 adapter scans 512-token chunks and reports the maximum malicious
probability; without model or dependencies it is unavailable (the verdict then fails closed)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from pkpipe.adapters.classifier_promptguard import (
    DIR_ENV,
    PromptGuardClassifier,
    chunk_ids,
    model_dir,
    softmax,
)
from pkpipe.ports import ClassifierPort, ClassifierUnavailable


class WordBackend:
    """One token per word; a chunk containing the word 'attack' gets logits favouring 'malicious'."""

    def __init__(self):
        self.vocab: dict[str, int] = {}
        self.batches: list[list[list[int]]] = []

    def tokenize(self, text: str) -> list[int]:
        return [self.vocab.setdefault(w, len(self.vocab) + 1) for w in text.split()]

    def malicious_index(self) -> int:
        return 1

    def logits(self, chunks):
        self.batches.append(chunks)
        bad = self.vocab.get("attack")
        return [[0.0, 4.0] if bad in c else [4.0, 0.0] for c in chunks]


def test_chunks_overlap_and_cover_every_token():
    ids = list(range(1000))
    chunks = chunk_ids(ids, 510, overlap=32)
    assert all(len(c) <= 510 for c in chunks)
    assert chunks[0][0] == 0 and chunks[-1][-1] == 999
    assert chunks[1][0] == 510 - 32  # overlap: an attack on a border is seen whole in one chunk
    assert set().union(*map(set, chunks)) == set(ids)
    assert chunk_ids([], 510) == [[]] and chunk_ids([1, 2], 510) == [[1, 2]]
    with pytest.raises(ValueError):
        chunk_ids(ids, 0)


def test_softmax():
    p = softmax([0.0, math.log(3)])
    assert p == pytest.approx([0.25, 0.75])


def test_scan_reports_the_maximum_over_chunks():
    backend = WordBackend()
    clf = PromptGuardClassifier(chunk_tokens=12, backend=backend, batch=2)
    text = " ".join(["benign"] * 40 + ["attack"] + ["benign"] * 40)
    res = clf.scan(text)
    assert isinstance(clf, ClassifierPort)
    assert res["chunks"] == len(res["scores"]) > 4
    assert res["max_score"] == pytest.approx(softmax([0.0, 4.0])[1], abs=1e-6)
    assert min(res["scores"]) < 0.05
    assert all(len(b) <= 2 for b in backend.batches)  # batched
    assert all(len(c) <= 10 for b in backend.batches for c in b)  # room for 2 special tokens


def test_missing_model_directory_is_unavailable(tmp_path):
    clf = PromptGuardClassifier(tmp_path / "absent")
    assert clf.available() is False
    with pytest.raises(ClassifierUnavailable, match="not found"):
        clf.scan("text")


def test_no_directory_is_unavailable():
    with pytest.raises(ClassifierUnavailable):
        PromptGuardClassifier(None).scan("x")


def test_model_dir_resolution(tmp_path):
    assert model_dir({"model_dir": str(tmp_path)}, {}) == tmp_path
    assert model_dir({"model_dir": "env:PG|/fallback"}, {"PG": str(tmp_path)}) == tmp_path
    assert model_dir({}, {DIR_ENV: str(tmp_path / "x")}) == tmp_path / "x"
    default = model_dir({"model": "meta-llama/Llama-Prompt-Guard-2-86M"}, {})
    assert default == Path("~/tools/prompt-guard-2/Llama-Prompt-Guard-2-86M").expanduser()


def test_from_config_uses_chunk_tokens(tmp_path):
    clf = PromptGuardClassifier.from_config(
        {"prompt_guard": {"model": "m/x", "threshold": 0.5, "chunk_tokens": 256, "model_dir": str(tmp_path)}}
    )
    assert clf.chunk_tokens == 256 and clf.directory == tmp_path and clf.model == "m/x"
