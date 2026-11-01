"""Recorded model responses (cassettes) for ``ModelPort`` (spec 013 US3, T003).

:class:`CassetteModel` wraps any ``ModelPort``:

* ``record`` – call the real model and store every request/response pair;
* ``replay`` – answer only from the cassette, never touching the network; a request without a recording raises
  :class:`CassetteMiss` (a ``ModelError``), so a changed prompt is visible instead of silently "passing";
* ``auto``   – replay what is recorded, call the real model (and record) for the rest.

Prompts contain random datamarking tokens (``<<<DATA 1a2b3c4d5e6f>>>``, constitution IV) that differ on every
call; the cassette key normalises them, and the run canary of spec 009 (``CNY-<hex>``) likewise, so the same
logical request always finds its recording. Model errors
(``SchemaError``, ...) are recorded too and replayed as the same exception type.

:class:`CassetteClassifier` does the same for the local ``ClassifierPort`` of the security suite (spec 009).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any, Literal

from pkpipe.ports import (
    ClassifierPort,
    ClassifierUnavailable,
    ContextOverflow,
    ModelError,
    ModelPort,
    ModelUnavailable,
    SchemaError,
)

Mode = Literal["record", "replay", "auto"]
FORMAT = 1
_TOKEN = re.compile(r"<<<(?:END )?DATA ([0-9a-f]{6,64})>>>")
#: The run canary of a ``canary: true`` session (spec 009) differs per run like the datamarking tokens.
_CANARY = re.compile(r"CNY-[0-9a-f]{16}")
_ERRORS: dict[str, type[ModelError]] = {
    "SchemaError": SchemaError,
    "ModelUnavailable": ModelUnavailable,
    "ContextOverflow": ContextOverflow,
    "ModelError": ModelError,
}


class CassetteMiss(ModelError):
    """Replay mode and no recording for this request (the prompt, schema or profile changed)."""


def normalise(text: str) -> str:
    """Replace every datamarking token and run canary in ``text`` by fixed placeholders."""
    for token in set(_TOKEN.findall(text)):
        text = text.replace(token, "TOKEN")
    return _CANARY.sub("CANARY", text)


def request_key(system: str, user: str, json_schema: dict[str, Any] | None, profile: str) -> str:
    # the system prompt's datamark notice names the token of the user prompt: normalise both together
    tokens = set(_TOKEN.findall(user)) | set(_TOKEN.findall(system))
    for token in tokens:
        system, user = system.replace(token, "TOKEN"), user.replace(token, "TOKEN")
    system, user = _CANARY.sub("CANARY", system), _CANARY.sub("CANARY", user)
    canonical = json.dumps(
        {"system": system, "user": user, "schema": json_schema, "profile": profile},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CassetteModel:
    """``ModelPort`` backed by a cassette file (see module docstring)."""

    def __init__(
        self,
        path: Path,
        mode: Mode = "replay",
        inner: ModelPort | None = None,
        *,
        identities: dict[str, str] | None = None,
    ):
        if mode not in ("record", "replay", "auto"):
            raise ValueError(f"unknown cassette mode '{mode}'")
        if mode != "replay" and inner is None:
            raise ValueError(f"cassette mode '{mode}' needs the real model to record from")
        self.path = path
        self.mode = mode
        self.inner = inner
        self.identities = identities or {}  # profile -> model identity, stored with each recording
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self.hits = 0
        self.misses = 0
        self.recorded = 0
        self.used: set[str] = set()  # keys requested in this session (a full re-record prunes the rest)
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("format") != FORMAT:
                raise ValueError(f"{path}: unsupported cassette format {data.get('format')!r}")
            self._entries = {e["key"]: e for e in data.get("entries", [])}

    def __len__(self) -> int:
        return len(self._entries)

    def complete(
        self,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None = None,
        profile: str = "default",
    ) -> dict[str, Any]:
        key = request_key(system, user, json_schema, profile)
        with self._lock:
            self.used.add(key)
            entry = self._entries.get(key) if self.mode != "record" else None
            if entry is not None:
                self.hits += 1
        if entry is not None:
            return self._play(entry)
        if self.mode == "replay":
            with self._lock:
                self.misses += 1
            raise CassetteMiss(
                f"no recording in {self.path.name} for this request (profile '{profile}'); "
                "re-record with: pkpipe eval --cassette record"
            )
        assert self.inner is not None
        entry = {
            "key": key,
            "profile": profile,
            "model": self.identities.get(profile),
            "request": {"system": normalise(system), "user": normalise(user), "json_schema": json_schema},
        }
        try:
            reply = self.inner.complete(system, user, json_schema, profile=profile)
            entry["response"] = reply
        except ModelError as exc:
            if isinstance(exc, CassetteMiss | ModelUnavailable):
                raise  # an unreachable server is not a recording
            entry["error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
        with self._lock:
            self._entries[key] = entry
            self._dirty = True
            self.recorded += 1
        return self._play(entry)

    @staticmethod
    def _play(entry: dict[str, Any]) -> dict[str, Any]:
        if "error" in entry:
            err = entry["error"]
            raise _ERRORS.get(err.get("type"), ModelError)(err.get("message", "recorded model error"))
        return copy.deepcopy(entry["response"])

    def save(self, *, prune_to: set[str] | None = None) -> bool:
        """Write the cassette (sorted, stable diff). ``prune_to`` keeps only those keys (a full re-record).
        Returns whether the file changed."""
        with self._lock:
            entries = self._entries
            if prune_to is not None:
                entries = {k: v for k, v in entries.items() if k in prune_to}
                self._dirty = self._dirty or len(entries) != len(self._entries)
                self._entries = entries
            if not self._dirty:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"format": FORMAT, "entries": [entries[k] for k in sorted(entries)]}
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(self.path)
            self._dirty = False
            return True

    def keys(self) -> set[str]:
        with self._lock:
            return set(self._entries)


class ClassifierMiss(ClassifierUnavailable):
    """Replay mode and no recorded classifier result for this text (text or classifier config changed)."""


class CassetteClassifier:
    """``ClassifierPort`` backed by a cassette (spec 009): the security suite measures the local classifier
    (Qwen3Guard, DeBERTa) live and replays the recorded results in CI, where the models are not installed.

    The key is the classifier configuration plus the text, so a changed model, chunk size or text is a miss
    (``ClassifierMiss``, which the suite reports as an error, never as a pass). Only results are stored."""

    def __init__(self, path: Path, mode: Mode, inner: ClassifierPort | None, *, identity: str):
        if mode != "replay" and inner is None:
            raise ValueError(f"cassette mode '{mode}' needs the real classifier to record from")
        self.path, self.mode, self.inner, self.identity = path, mode, inner, identity
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self.hits = self.misses = self.recorded = 0
        self.used: set[str] = set()
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("format") != FORMAT:
                raise ValueError(f"{path}: unsupported cassette format {data.get('format')!r}")
            self._entries = {e["key"]: e for e in data.get("entries", [])}

    def key(self, text: str) -> str:
        return hashlib.sha256(f"{self.identity}\n{text}".encode()).hexdigest()

    def scan(self, text: str) -> dict[str, Any]:
        key = self.key(text or "")
        with self._lock:
            self.used.add(key)
            entry = self._entries.get(key) if self.mode != "record" else None
            if entry is not None:
                self.hits += 1
                return copy.deepcopy(entry["result"])
            if self.mode == "replay":
                self.misses += 1
                raise ClassifierMiss(f"no classifier recording in {self.path.name} for this text")
        assert self.inner is not None
        result = self.inner.scan(text)
        with self._lock:
            self._entries[key] = {"key": key, "result": result}
            self._dirty = True
            self.recorded += 1
        return copy.deepcopy(result)

    def save(self, *, prune_to: set[str] | None = None) -> bool:
        with self._lock:
            entries = self._entries
            if prune_to is not None:
                entries = {k: v for k, v in entries.items() if k in prune_to}
                self._dirty = self._dirty or len(entries) != len(self._entries)
                self._entries = entries
            if not self._dirty:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "format": FORMAT,
                "classifier": self.identity,
                "entries": [entries[k] for k in sorted(entries)],
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(self.path)
            self._dirty = False
            return True
