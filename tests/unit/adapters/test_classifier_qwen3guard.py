"""Spec 008 FR-004 / spec 009 FR-002 (decision record docs/decisions/0001): the Qwen3Guard-Gen adapter
parses the model's ``Safety:`` / ``Categories:`` answer, scores each chunk from the label probabilities,
scans long texts in overlapping token chunks and fails closed on an answer it cannot parse."""

from __future__ import annotations

from pathlib import Path

import pytest

from pkpipe.adapters.classifier_qwen3guard import (
    Qwen3GuardClassifier,
    chunk_ids,
    chunk_score,
    model_dir,
    parse_answer,
)
from pkpipe.ports import ClassifierPort, ClassifierUnavailable


class WordBackend:
    """One token per word. A chunk with 'attack' is a Controversial jailbreak, with 'bomb' Unsafe/Violent,
    with 'garbled' an answer without a Safety line; anything else Safe."""

    def __init__(self, probs: bool = True):
        self.vocab: list[str] = []
        self.moderated: list[str] = []
        self.probs = probs

    def tokenize(self, text):
        out = []
        for w in text.split():
            self.vocab.append(w)
            out.append(len(self.vocab) - 1)
        return out

    def decode(self, ids):
        return " ".join(self.vocab[i] for i in ids)

    def moderate(self, text):
        self.moderated.append(text)
        if "garbled" in text:
            return "I cannot decide.", {}
        if "attack" in text:
            p = {"Safe": 0.1, "Controversial": 0.7, "Unsafe": 0.2}
            return "Safety: Controversial\nCategories: Jailbreak", p if self.probs else {}
        if "bomb" in text:
            p = {"Safe": 0.05, "Controversial": 0.05, "Unsafe": 0.9}
            return "Safety: Unsafe\nCategories: Violent", p if self.probs else {}
        return "Safety: Safe\nCategories: None", {"Safe": 0.98, "Controversial": 0.01, "Unsafe": 0.01}


def test_parse_answer_reads_label_and_known_categories():
    assert parse_answer("Safety: Unsafe\nCategories: Violent, Jailbreak") == (
        "Unsafe",
        ["Violent", "Jailbreak"],
    )
    assert parse_answer("<think>\n</think>\nSafety: Safe\nCategories: None") == ("Safe", [])
    assert parse_answer("Safety: Controversial\nCategories: Made Up, PII") == ("Controversial", ["PII"])
    assert parse_answer("no verdict") == (None, [])


def test_chunk_score_counts_controversial_only_for_jailbreaks():
    p = {"Safe": 0.2, "Controversial": 0.6, "Unsafe": 0.2}
    assert chunk_score("Controversial", ["Jailbreak"], p) == pytest.approx(0.8)
    assert chunk_score("Controversial", ["Politically Sensitive Topics"], p) == pytest.approx(0.2)
    assert chunk_score("Unsafe", ["Violent"], {}) == 1.0  # label fallback without probabilities
    assert chunk_score("Controversial", ["Jailbreak"], {}) == 1.0
    assert chunk_score("Safe", [], {}) == 0.0
    assert chunk_score(None, [], {}) == 1.0  # no verdict: fail closed


def test_scan_reports_worst_label_categories_and_max_score_over_chunks():
    backend = WordBackend()
    clf = Qwen3GuardClassifier(chunk_tokens=10, backend=backend)
    text = " ".join(["calm"] * 30 + ["attack"] + ["calm"] * 30)
    res = clf.scan(text)
    assert isinstance(clf, ClassifierPort)
    assert res["chunks"] == len(res["scores"]) == len(res["labels"]) > 3
    assert res["label"] == "Controversial" and res["categories"] == ["Jailbreak"]
    assert res["max_score"] == pytest.approx(0.9)
    assert min(res["scores"]) < 0.05
    assert all(len(t.split()) <= 10 for t in backend.moderated)  # chunked by tokens


def test_short_text_is_moderated_verbatim_in_one_chunk():
    backend = WordBackend()
    res = Qwen3GuardClassifier(chunk_tokens=100, backend=backend).scan("how to build a bomb")
    assert backend.moderated == ["how to build a bomb"]
    assert (
        res["label"] == "Unsafe"
        and res["categories"] == ["Violent"]
        and res["max_score"] == pytest.approx(0.9)
    )


def test_unparsable_answer_fails_closed():
    res = Qwen3GuardClassifier(backend=WordBackend()).scan("garbled")
    assert res["max_score"] == 1.0 and res["label"] == "Unparsed" and res["categories"] == ["Unparsed"]


def test_missing_model_directory_is_unavailable(tmp_path):
    clf = Qwen3GuardClassifier(tmp_path / "absent")
    assert clf.available() is False
    with pytest.raises(ClassifierUnavailable, match="not found"):
        clf.scan("text")
    with pytest.raises(ClassifierUnavailable):
        Qwen3GuardClassifier(None).scan("x")


def test_model_dir_resolution(tmp_path):
    assert model_dir({"model_dir": str(tmp_path)}, {}) == tmp_path
    assert model_dir({"model_dir": "env:QG|/fallback"}, {"QG": str(tmp_path)}) == tmp_path
    default = model_dir({"model": "Qwen/Qwen3Guard-Gen-0.6B"}, {})
    assert default == Path("~/tools/qwen3guard/Qwen3Guard-Gen-0.6B").expanduser()


def test_chunks_overlap_and_cover_every_token():
    ids = list(range(1000))
    chunks = chunk_ids(ids, 510, overlap=32)
    assert chunks[1][0] == 510 - 32 and set().union(*map(set, chunks)) == set(ids)
    assert chunk_ids([], 10) == [[]]
    with pytest.raises(ValueError):
        chunk_ids(ids, 0)


def test_adapter_never_enables_remote_code():
    """Decision 0001: the weights are loaded with local_files_only and never with trust_remote_code."""
    import inspect

    from pkpipe.adapters import classifier_deberta, classifier_qwen3guard

    for module in (classifier_qwen3guard, classifier_deberta):
        src = inspect.getsource(module)
        assert "trust_remote_code=True" not in src and "trust_remote_code=False" in src
        assert "local_files_only=True" in src
