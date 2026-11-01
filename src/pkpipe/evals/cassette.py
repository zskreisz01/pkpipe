"""Recorded model responses (cassettes) for ``ModelPort`` (spec 013 US3, T003).

:class:`CassetteModel` wraps any ``ModelPort``:

* ``record`` – call the real model and store every request/response pair;
* ``replay`` – answer only from the cassette, never touching the network; a request without a recording raises
  :class:`CassetteMiss` (a ``ModelError``), so a changed prompt is visible instead of silently "passing";
* ``auto``   – replay what is recorded, call the real model (and record) for the rest.

Prompts contain random datamarking tokens (``<<<DATA 1a2b3c4d5e6f>>>``, constitution IV) that differ on every
call; the cassette key normalises them, so the same logical request always finds its recording. Model errors
(``SchemaError``, ...) are recorded too and replayed as the same exception type.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any, Literal

from pkpipe.ports import ContextOverflow, ModelError, ModelPort, ModelUnavailable, SchemaError

Mode = Literal["record", "replay", "auto"]
FORMAT = 1
_TOKEN = re.compile(r"<<<(?:END )?DATA ([0-9a-f]{6,64})>>>")
_ERRORS: dict[str, type[ModelError]] = {
    "SchemaError": SchemaError,
    "ModelUnavailable": ModelUnavailable,
    "ContextOverflow": ContextOverflow,
    "ModelError": ModelError,
}


class CassetteMiss(ModelError):
    """Replay mode and no recording for this request (the prompt, schema or profile changed)."""


def normalise(text: str) -> str:
    """Replace every datamarking token in ``text`` by a fixed placeholder."""
    for token in set(_TOKEN.findall(text)):
        text = text.replace(token, "TOKEN")
    return text


def request_key(system: str, user: str, json_schema: dict[str, Any] | None, profile: str) -> str:
    # the system prompt's datamark notice names the token of the user prompt: normalise both together
    tokens = set(_TOKEN.findall(user)) | set(_TOKEN.findall(system))
    for token in tokens:
        system, user = system.replace(token, "TOKEN"), user.replace(token, "TOKEN")
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
