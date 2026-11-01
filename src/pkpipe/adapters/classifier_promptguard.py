"""ClassifierPort on Llama Prompt Guard 2 (86M), local and offline (spec 008 FR-004).

The text is tokenized once and scanned in chunks of ``chunk_tokens`` (512, the model's window) with a small
overlap, so an attack split across a chunk border is still seen whole in one chunk. Each chunk's score is
the softmax probability of the "malicious" label; the result is the maximum over all chunks. Whether that
is a flag is decided by the verdict code (``security.prompt_guard.threshold``), not here.

Heavy dependencies (``torch``, ``transformers``) are the optional extra ``pkpipe[promptguard]``; the model is
gated on Hugging Face, so it is read from a local directory only (``security.prompt_guard.model_dir``,
``$PKPIPE_PROMPT_GUARD_DIR`` or ``~/tools/prompt-guard-2/<model name>``) and never downloaded at run time.
Missing model or dependencies raise :class:`ClassifierUnavailable`, and the verdict fails closed.
"""

from __future__ import annotations

import math
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from pkpipe.adapters.model_openai import resolve_value
from pkpipe.ports import ClassifierUnavailable

DIR_ENV = "PKPIPE_PROMPT_GUARD_DIR"
DEFAULT_MODEL = "meta-llama/Llama-Prompt-Guard-2-86M"
DEFAULT_ROOT = Path("~/tools/prompt-guard-2")
OVERLAP_TOKENS = 32


class Backend(Protocol):
    """The model behind the classifier: token ids in, per-chunk logits out."""

    def tokenize(self, text: str) -> list[int]: ...

    def logits(self, chunks: list[list[int]]) -> list[list[float]]: ...

    def malicious_index(self) -> int: ...


def model_dir(prompt_guard: Mapping[str, Any] | None, environ: Mapping[str, str] | None = None) -> Path:
    """Where the model is expected: config ``model_dir`` (``env:`` allowed), then the env var, then the
    default ``~/tools/prompt-guard-2/<last part of the model name>``."""
    env = os.environ if environ is None else environ
    cfg = dict(prompt_guard or {})
    if cfg.get("model_dir"):
        return Path(resolve_value(str(cfg["model_dir"]), env)).expanduser()
    if env.get(DIR_ENV):
        return Path(env[DIR_ENV]).expanduser()
    name = str(cfg.get("model") or DEFAULT_MODEL).rsplit("/", 1)[-1]
    return (DEFAULT_ROOT / name).expanduser()


def chunk_ids(ids: list[int], size: int, overlap: int = OVERLAP_TOKENS) -> list[list[int]]:
    """Windows of ``size`` tokens that overlap by ``overlap`` (at least one, possibly empty, chunk)."""
    if size <= 0:
        raise ValueError("chunk size must be positive")
    if len(ids) <= size:
        return [list(ids)]
    step = max(size - overlap, 1)
    out = []
    for start in range(0, len(ids), step):
        out.append(ids[start : start + size])
        if start + size >= len(ids):
            break
    return out


def softmax(row: list[float]) -> list[float]:
    m = max(row)
    exps = [math.exp(x - m) for x in row]
    total = sum(exps)
    return [e / total for e in exps]


class TransformersBackend:
    """Loads tokenizer and model from a local directory (``local_files_only``), CPU inference."""

    def __init__(self, directory: Path):
        if not directory.is_dir():
            raise ClassifierUnavailable(f"Prompt Guard model directory not found: {directory}")
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:
            raise ClassifierUnavailable(
                "Prompt Guard needs the optional extra: uv sync --extra promptguard"
            ) from exc
        self._torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(str(directory), local_files_only=True)
        self.model = AutoModelForSequenceClassification.from_pretrained(str(directory), local_files_only=True)
        self.model.eval()
        self._lock = threading.Lock()

    def tokenize(self, text: str) -> list[int]:
        return list(self.tokenizer(text, add_special_tokens=False)["input_ids"])

    def malicious_index(self) -> int:
        labels = {str(v).lower(): int(k) for k, v in (self.model.config.id2label or {}).items()}
        for name in ("malicious", "label_1", "jailbreak", "injection"):
            if name in labels:
                return labels[name]
        return len(labels) - 1 if labels else 1

    def logits(self, chunks: list[list[int]]) -> list[list[float]]:
        torch = self._torch
        cls, sep = self.tokenizer.cls_token_id, self.tokenizer.sep_token_id
        rows = [
            [x for x in ([cls] if cls is not None else []) + c + ([sep] if sep is not None else [])]
            for c in chunks
        ]
        width = max(len(r) for r in rows)
        pad = self.tokenizer.pad_token_id or 0
        ids = torch.tensor([r + [pad] * (width - len(r)) for r in rows])
        mask = torch.tensor([[1] * len(r) + [0] * (width - len(r)) for r in rows])
        with self._lock, torch.no_grad():
            out = self.model(input_ids=ids, attention_mask=mask).logits
        return out.tolist()


class PromptGuardClassifier:
    """ClassifierPort. ``backend`` is built lazily from ``directory`` (tests pass their own backend)."""

    def __init__(
        self,
        directory: Path | None = None,
        *,
        chunk_tokens: int = 512,
        model: str = DEFAULT_MODEL,
        backend: Backend | None = None,
        batch: int = 8,
    ):
        self.directory = directory
        self.chunk_tokens = chunk_tokens
        self.model = model
        self.batch = batch
        self._backend = backend
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, security: Mapping[str, Any] | None, environ=None) -> PromptGuardClassifier:
        pg = dict((security or {}).get("prompt_guard") or {})
        return cls(
            model_dir(pg, environ),
            chunk_tokens=int(pg.get("chunk_tokens", 512)),
            model=str(pg.get("model") or DEFAULT_MODEL),
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
                    raise ClassifierUnavailable("no Prompt Guard model directory configured")
                self._backend = TransformersBackend(self.directory)
            return self._backend

    def scan(self, text: str) -> dict[str, Any]:
        backend = self._load()
        # room for the special tokens the backend adds around each chunk
        chunks = chunk_ids(backend.tokenize(text or ""), max(self.chunk_tokens - 2, 1))
        bad = backend.malicious_index()
        scores: list[float] = []
        for i in range(0, len(chunks), self.batch):
            for row in backend.logits(chunks[i : i + self.batch]):
                scores.append(round(softmax(list(row))[bad], 6))
        return {"model": self.model, "max_score": max(scores), "chunks": len(chunks), "scores": scores}
