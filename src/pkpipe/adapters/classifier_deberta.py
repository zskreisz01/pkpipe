"""ClassifierPort on ProtectAI ``deberta-v3-base-prompt-injection-v2`` (Apache-2.0), local and offline
(spec 008 FR-004, spec 009 FR-002; decision record ``docs/decisions/0001-injection-classifier.md``).

A binary sequence classifier (``SAFE`` / ``INJECTION``) with a 512-token window. The text is tokenized once
and scanned in chunks of ``chunk_tokens`` with a small overlap, so an attack split across a chunk border is
still seen whole in one chunk. Each chunk's score is the softmax probability of the ``INJECTION`` label; the
result is the maximum over all chunks. Whether that is a flag is decided by code
(``security.classifier.threshold``), not here.

Heavy dependencies (``torch``, ``transformers``) are the optional extra ``pkpipe[guard]``. Only the vetted
safetensors weights are read from a local directory (``local_files_only``, ``use_safetensors``, never
``trust_remote_code``), never downloaded at run time. Missing model or dependencies raise
:class:`ClassifierUnavailable`, and the callers fail closed.
"""

from __future__ import annotations

import math
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from pkpipe.adapters.classifier_qwen3guard import chunk_ids
from pkpipe.adapters.model_openai import resolve_value
from pkpipe.ports import ClassifierUnavailable

DEFAULT_MODEL = "protectai/deberta-v3-base-prompt-injection-v2"
DEFAULT_DIR = Path("~/tools/protectai-deberta-pi-v2")
WINDOW = 512


class Backend(Protocol):
    """The model behind the classifier: token ids in, per-chunk logits out."""

    def tokenize(self, text: str) -> list[int]: ...

    def logits(self, chunks: list[list[int]]) -> list[list[float]]: ...

    def malicious_index(self) -> int: ...


def model_dir(classifier: Mapping[str, Any] | None, environ: Mapping[str, str] | None = None) -> Path:
    """Config ``model_dir`` (``env:NAME|default`` allowed), else ``~/tools/protectai-deberta-pi-v2``."""
    cfg = dict(classifier or {})
    if cfg.get("model_dir"):
        env = os.environ if environ is None else environ
        return Path(resolve_value(str(cfg["model_dir"]), env)).expanduser()
    return DEFAULT_DIR.expanduser()


def softmax(row: list[float]) -> list[float]:
    m = max(row)
    exps = [math.exp(x - m) for x in row]
    total = sum(exps)
    return [e / total for e in exps]


class TransformersBackend:
    """Tokenizer and sequence classifier from a local directory, CPU or MPS inference."""

    def __init__(self, directory: Path):
        if not directory.is_dir():
            raise ClassifierUnavailable(f"injection classifier directory not found: {directory}")
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:
            raise ClassifierUnavailable(
                "the injection classifier needs the optional extra: uv sync --extra guard"
            ) from exc
        self._torch = torch
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(
                str(directory), local_files_only=True, trust_remote_code=False
            )
            self.model = AutoModelForSequenceClassification.from_pretrained(
                str(directory), local_files_only=True, trust_remote_code=False, use_safetensors=True
            )
        except (OSError, ValueError) as exc:
            raise ClassifierUnavailable(f"injection classifier could not be loaded: {exc}") from exc
        self.model.eval()
        self._lock = threading.Lock()

    def tokenize(self, text: str) -> list[int]:
        return list(self.tokenizer(text, add_special_tokens=False)["input_ids"])

    def malicious_index(self) -> int:
        labels = {str(v).lower(): int(k) for k, v in (self.model.config.id2label or {}).items()}
        for name in ("injection", "malicious", "jailbreak", "label_1"):
            if name in labels:
                return labels[name]
        return len(labels) - 1 if labels else 1

    def logits(self, chunks: list[list[int]]) -> list[list[float]]:
        torch = self._torch
        cls, sep = self.tokenizer.cls_token_id, self.tokenizer.sep_token_id
        rows = [([cls] if cls is not None else []) + c + ([sep] if sep is not None else []) for c in chunks]
        width = max(len(r) for r in rows)
        pad = self.tokenizer.pad_token_id or 0
        ids = torch.tensor([r + [pad] * (width - len(r)) for r in rows])
        mask = torch.tensor([[1] * len(r) + [0] * (width - len(r)) for r in rows])
        with self._lock, torch.no_grad():
            out = self.model(input_ids=ids, attention_mask=mask).logits
        return out.tolist()


class DebertaInjectionClassifier:
    """ClassifierPort. ``backend`` is built lazily from ``directory`` (tests pass their own backend)."""

    def __init__(
        self,
        directory: Path | None = None,
        *,
        chunk_tokens: int = WINDOW,
        model: str = DEFAULT_MODEL,
        backend: Backend | None = None,
        batch: int = 8,
    ):
        self.directory = directory
        self.chunk_tokens = min(chunk_tokens, WINDOW)
        self.model = model
        self.batch = batch
        self._backend = backend
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, classifier: Mapping[str, Any] | None, environ=None) -> DebertaInjectionClassifier:
        cfg = dict(classifier or {})
        return cls(
            model_dir(cfg, environ),
            chunk_tokens=int(cfg.get("chunk_tokens") or WINDOW),
            model=str(cfg.get("model") or DEFAULT_MODEL),
        )

    def available(self) -> bool:
        try:
            self._load()
            return True
        except ClassifierUnavailable:
            return False

    def _load(self) -> Backend:
        with self._lock:
            if self._backend is None:
                if self.directory is None:
                    raise ClassifierUnavailable("no injection classifier directory configured")
                self._backend = TransformersBackend(self.directory)
            return self._backend

    def scan(self, text: str) -> dict[str, Any]:
        backend = self._load()
        # room for the two special tokens the backend adds around each chunk
        chunks = chunk_ids(backend.tokenize(text or ""), max(self.chunk_tokens - 2, 1))
        bad = backend.malicious_index()
        scores: list[float] = []
        for i in range(0, len(chunks), self.batch):
            for row in backend.logits(chunks[i : i + self.batch]):
                scores.append(round(softmax(list(row))[bad], 6))
        # an injection detector only: no safety label or harm category (the caller's threshold decides)
        return {"model": self.model, "max_score": max(scores), "chunks": len(chunks), "scores": scores}
