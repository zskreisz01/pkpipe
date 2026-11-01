"""ClassifierPort on Qwen3Guard-Gen (Apache-2.0, official ``Qwen/`` weights), local and offline (spec 008
FR-004, spec 009 FR-002; decision record ``docs/decisions/0001-injection-classifier.md``).

Qwen3Guard-Gen is a generative safety moderator: for a user prompt it answers::

    Safety: Safe|Controversial|Unsafe
    Categories: Violent, ..., Jailbreak | None

pkpipe sends the text to check as the *user prompt* (prompt moderation), because that is the policy with the
``Jailbreak`` category, and both the page text of secure fetch and a stored note are text that a later model
reads as its input. The text is tokenized once and scanned in chunks of ``chunk_tokens`` with a small
overlap, so an attack split across a chunk border is still seen whole in one chunk.

Score per chunk, from the probabilities of the label token after ``Safety:`` (greedy decoding, so the score
is deterministic): ``P(Unsafe) + P(Controversial)`` when the chunk names ``Jailbreak`` (an injection is never
"just controversial"), else ``P(Unsafe)``. ``max_score`` is the maximum over the chunks. The adapter only
reports; whether a score, label or category is a flag is decided by code (``security.classifier.threshold``,
``security.output_guard.block_on``). An answer without a ``Safety:`` line scores 1.0 (fail closed).

Heavy dependencies (``torch``, ``transformers``) are the optional extra ``pkpipe[guard]``. The model is read
from a local directory only (``local_files_only``, never ``trust_remote_code``) and never downloaded at run
time. Missing model or dependencies raise :class:`ClassifierUnavailable`, and the callers fail closed.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from pkpipe.adapters.model_openai import resolve_value
from pkpipe.ports import ClassifierUnavailable

DEFAULT_MODEL = "Qwen/Qwen3Guard-Gen-0.6B"
DEFAULT_ROOT = Path("~/tools/qwen3guard")
DEFAULT_CHUNK_TOKENS = 1024
OVERLAP_TOKENS = 32
LABELS = ("Safe", "Controversial", "Unsafe")
#: The categories of the prompt-moderation policy (model card).
CATEGORIES = (
    "Violent",
    "Non-violent Illegal Acts",
    "Sexual Content or Sexual Acts",
    "PII",
    "Suicide & Self-Harm",
    "Unethical Acts",
    "Politically Sensitive Topics",
    "Copyright Violation",
    "Jailbreak",
)
JAILBREAK = "Jailbreak"
SEVERITY = {"Safe": 0, "Controversial": 1, "Unsafe": 2}
_SAFETY_RE = re.compile(r"Safety:\s*(Safe|Unsafe|Controversial)")
_CATEGORIES_RE = re.compile(r"Categories:\s*(.*)")


class GuardBackend(Protocol):
    """The model behind the classifier: token ids in and out, one moderation answer per text."""

    def tokenize(self, text: str) -> list[int]: ...

    def decode(self, ids: list[int]) -> str: ...

    def moderate(self, text: str) -> tuple[str, dict[str, float]]:
        """(generated answer, label probabilities ``{"Safe": p, "Controversial": p, "Unsafe": p}``; the
        probabilities may be empty when the backend cannot provide them)."""
        ...


def model_dir(classifier: Mapping[str, Any] | None, environ: Mapping[str, str] | None = None) -> Path:
    """Where the model is expected: config ``model_dir`` (``env:NAME|default`` allowed), else
    ``~/tools/qwen3guard/<last part of the model name>``."""
    cfg = dict(classifier or {})
    if cfg.get("model_dir"):
        import os

        env = os.environ if environ is None else environ
        return Path(resolve_value(str(cfg["model_dir"]), env)).expanduser()
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


def parse_answer(answer: str) -> tuple[str | None, list[str]]:
    """(label, categories) of a Qwen3Guard answer; ``None`` when there is no ``Safety:`` line. Only the
    known category names are kept (``None`` and anything else are dropped)."""
    text = re.sub(r"<think>.*?</think>", "", answer or "", flags=re.DOTALL)
    m = _SAFETY_RE.search(text)
    label = m.group(1) if m else None
    cats: list[str] = []
    c = _CATEGORIES_RE.search(text)
    if c:
        for part in c.group(1).split(","):
            name = part.strip().rstrip(".")
            if name in CATEGORIES and name not in cats:
                cats.append(name)
    return label, cats


def chunk_score(label: str | None, categories: list[str], probs: Mapping[str, float]) -> float:
    """The injection / harm score of one chunk (see the module docstring)."""
    if label is None:
        return 1.0  # no verdict: fail closed
    if probs:
        total = sum(max(float(probs.get(k, 0.0)), 0.0) for k in LABELS) or 1.0
        p = {k: max(float(probs.get(k, 0.0)), 0.0) / total for k in LABELS}
        score = p["Unsafe"] + (p["Controversial"] if JAILBREAK in categories else 0.0)
    else:
        score = {"Safe": 0.0, "Controversial": 0.5, "Unsafe": 1.0}[label]
        if JAILBREAK in categories and label != "Safe":
            score = 1.0
    return round(min(max(score, 0.0), 1.0), 6)


class TransformersGuardBackend:
    """Tokenizer and causal LM from a local directory (``local_files_only``, no remote code); MPS when
    available, else CPU. Greedy decoding of at most 32 tokens, guarded by a lock (one model, many runs)."""

    MAX_NEW_TOKENS = 32

    def __init__(self, directory: Path):
        if not directory.is_dir():
            raise ClassifierUnavailable(f"Qwen3Guard model directory not found: {directory}")
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise ClassifierUnavailable("Qwen3Guard needs the optional extra: uv sync --extra guard") from exc
        self._torch = torch
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(
                str(directory), local_files_only=True, trust_remote_code=False
            )
            device = "mps" if torch.backends.mps.is_available() else "cpu"
            dtype = torch.bfloat16 if device == "mps" else torch.float32
            self.model = AutoModelForCausalLM.from_pretrained(
                str(directory), local_files_only=True, trust_remote_code=False, dtype=dtype
            ).to(device)
        except (OSError, ValueError) as exc:
            raise ClassifierUnavailable(f"Qwen3Guard model could not be loaded: {exc}") from exc
        self.model.eval()
        self.device = device
        self._lock = threading.Lock()
        self._label_ids = {
            name: self.tokenizer.encode(" " + name, add_special_tokens=False)[0] for name in LABELS
        }

    def tokenize(self, text: str) -> list[int]:
        return list(self.tokenizer(text, add_special_tokens=False)["input_ids"])

    def decode(self, ids: list[int]) -> str:
        return self.tokenizer.decode(ids, skip_special_tokens=False)

    def moderate(self, text: str) -> tuple[str, dict[str, float]]:
        torch = self._torch
        prompt = self.tokenizer.apply_chat_template([{"role": "user", "content": text}], tokenize=False)
        inputs = self.tokenizer([prompt], return_tensors="pt").to(self.device)
        with self._lock, torch.no_grad():
            out = self.model.generate(
                **inputs,
                max_new_tokens=self.MAX_NEW_TOKENS,
                do_sample=False,
                output_scores=True,
                return_dict_in_generate=True,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        generated = out.sequences[0][inputs["input_ids"].shape[1] :].tolist()
        answer = self.tokenizer.decode(generated, skip_special_tokens=True)
        probs: dict[str, float] = {}
        # the label token is the first generated token after "Safety" ":"
        pieces = [self.tokenizer.decode([t]) for t in generated]
        for i, piece in enumerate(pieces[:-1]):
            after_safety = i >= 1 and pieces[i - 1].strip() == "Safety"
            if piece.strip() == ":" and after_safety and i + 1 < len(out.scores):
                row = torch.softmax(out.scores[i + 1][0].float(), dim=-1)
                probs = {name: float(row[tid]) for name, tid in self._label_ids.items()}
                break
        return answer, probs


class Qwen3GuardClassifier:
    """ClassifierPort. ``backend`` is built lazily from ``directory`` (tests pass their own backend).

    ``scan`` returns ``{"model", "max_score", "chunks", "scores", "label", "labels", "categories"}``:
    ``label`` is the most severe label over the chunks, ``categories`` the union of the categories of the
    chunks that are not ``Safe``."""

    def __init__(
        self,
        directory: Path | None = None,
        *,
        chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
        model: str = DEFAULT_MODEL,
        backend: GuardBackend | None = None,
    ):
        self.directory = directory
        self.chunk_tokens = chunk_tokens
        self.model = model
        self._backend = backend
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, classifier: Mapping[str, Any] | None, environ=None) -> Qwen3GuardClassifier:
        cfg = dict(classifier or {})
        return cls(
            model_dir(cfg, environ),
            chunk_tokens=int(cfg.get("chunk_tokens") or DEFAULT_CHUNK_TOKENS),
            model=str(cfg.get("model") or DEFAULT_MODEL),
        )

    def available(self) -> bool:
        try:
            self._load()
            return True
        except ClassifierUnavailable:
            return False

    def _load(self) -> GuardBackend:
        with self._lock:
            if self._backend is None:
                if self.directory is None:
                    raise ClassifierUnavailable("no Qwen3Guard model directory configured")
                self._backend = TransformersGuardBackend(self.directory)
            return self._backend

    def scan(self, text: str) -> dict[str, Any]:
        backend = self._load()
        ids = backend.tokenize(text or "")
        chunks = chunk_ids(ids, max(self.chunk_tokens, 1))
        scores: list[float] = []
        labels: list[str] = []
        categories: list[str] = []
        for chunk in chunks:
            answer, probs = backend.moderate(backend.decode(chunk) if len(chunks) > 1 else (text or ""))
            label, cats = parse_answer(answer)
            scores.append(chunk_score(label, cats, probs))
            labels.append(label or "Unparsed")
            if label != "Safe":
                categories += [c for c in (cats or ([] if label else ["Unparsed"])) if c not in categories]
        worst = max(labels, key=lambda name: SEVERITY.get(name, 3))
        return {
            "model": self.model,
            "max_score": max(scores),
            "chunks": len(chunks),
            "scores": scores,
            "label": worst,
            "labels": labels,
            "categories": categories,
        }
