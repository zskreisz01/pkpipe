"""ClassifierPort fake: scores text by substring rules, chunked like the real adapter (spec 008 FR-004)."""

from __future__ import annotations

from typing import Any

from pkpipe.ports import ClassifierUnavailable


class FakeClassifier:
    """``rules`` maps a lower-case substring to a score; a chunk scores the highest matching rule (else
    ``default``). Chunks are ``chunk_chars`` characters, so long texts are scanned piecewise like the
    token chunks of the real classifiers. ``labels`` maps a lower-case substring to a safety label and
    categories (Qwen3Guard style: ``{"bomb": ("Unsafe", ["Violent"])}``); a chunk without a match is
    ``Safe``. ``available=False`` raises :class:`ClassifierUnavailable`."""

    def __init__(
        self,
        rules: dict[str, float] | None = None,
        *,
        default: float = 0.01,
        chunk_chars: int = 2000,
        available: bool = True,
        labels: dict[str, tuple[str, list[str]]] | None = None,
    ):
        self.rules = {k.lower(): v for k, v in (rules or {}).items()}
        self.labels = {k.lower(): v for k, v in (labels or {}).items()}
        self.default = default
        self.chunk_chars = chunk_chars
        self.available = available
        self.calls: list[str] = []

    def scan(self, text: str) -> dict[str, Any]:
        if not self.available:
            raise ClassifierUnavailable("fake classifier switched off")
        self.calls.append(text)
        chunks = [text[i : i + self.chunk_chars] for i in range(0, len(text), self.chunk_chars)] or [""]
        scores = [
            max([v for k, v in self.rules.items() if k in c.lower()], default=self.default) for c in chunks
        ]
        out: dict[str, Any] = {
            "model": "fake",
            "max_score": max(scores),
            "chunks": len(chunks),
            "scores": scores,
        }
        if self.labels:
            severity = {"Safe": 0, "Controversial": 1, "Unsafe": 2}
            label, categories = "Safe", []
            for key, (lab, cats) in self.labels.items():
                if key in text.lower():
                    if severity[lab] > severity[label]:
                        label = lab
                    categories += [c for c in cats if c not in categories]
            out.update(label=label, categories=categories)
        return out
