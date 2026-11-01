"""ClassifierPort fake: scores text by substring rules, chunked like the real adapter (spec 008 FR-004)."""

from __future__ import annotations

from typing import Any

from pkpipe.ports import ClassifierUnavailable


class FakeClassifier:
    """``rules`` maps a lower-case substring to a score; a chunk scores the highest matching rule (else
    ``default``). Chunks are ``chunk_chars`` characters, so long texts are scanned piecewise like the
    512-token chunks of Prompt Guard 2. ``available=False`` raises :class:`ClassifierUnavailable`."""

    def __init__(
        self,
        rules: dict[str, float] | None = None,
        *,
        default: float = 0.01,
        chunk_chars: int = 2000,
        available: bool = True,
    ):
        self.rules = {k.lower(): v for k, v in (rules or {}).items()}
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
        return {"model": "fake", "max_score": max(scores), "chunks": len(chunks), "scores": scores}
