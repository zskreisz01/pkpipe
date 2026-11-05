"""Recorded model responses (cassettes) for ``ModelPort`` (spec 013 US3, T003).

:class:`CassetteModel` wraps any ``ModelPort``:

* ``record`` – call the real model and store every request/response pair;
* ``replay`` – answer only from the cassette, never touching the network; a request without a recording raises
  :class:`CassetteMiss` (a ``ModelError``), so a changed prompt is visible instead of silently "passing";
* ``auto``   – replay what is recorded, call the real model (and record) for the rest.

Prompts contain random datamarking tokens (``<<<DATA 1a2b3c4d5e6f>>>``, constitution IV) that differ on every
call; the cassette key normalises them, and the run canary of spec 009 (``CNY-<hex>``) likewise, so the same
logical request always finds its recording. The model's own failures (``SchemaError``,
``ContextOverflow``) are recorded too and replayed as the same exception type; an unreachable server or a
refused request (plain ``ModelError``, e.g. HTTP 404 for an unknown model id) is raised, never recorded.

The key also holds the **model id** of the profile (``identities``: profile -> ``base_url|model``), so a
recording answers only the model it was recorded with. A request recorded for another model is not a plain
miss but :class:`CassetteModelMismatch`, naming both models. The base URL is *not* part of the key: it is
where a model is served (host, port, ``/v1``), which differs between machines, CI (``http://127.0.0.1:9/v1``)
and a one-model-per-port server, while the answers depend on the weights the model id names (quantisation
included, e.g. ``-mlx``). It is stored with each entry as ``server`` for the record. Format 1 cassettes
(key without the model id) are re-keyed offline by :func:`migrate_v1`
(``python -m pkpipe.evals.migrate_cassettes <file>...``).

:class:`CassetteClassifier` does the same for the local ``ClassifierPort`` of the security suite (spec 009).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pkpipe.ports import (
    ClassifierPort,
    ClassifierUnavailable,
    ContextOverflow,
    ImageInput,
    ModelError,
    ModelPort,
    ModelUnavailable,
    SchemaError,
)

Mode = Literal["record", "replay", "auto"]
#: Model cassettes: 2 = the key holds the model id (1 = request only, see :func:`migrate_v1`).
FORMAT = 2
#: Classifier cassettes (their key always held the classifier configuration).
CLASSIFIER_FORMAT = 1
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


class CassetteModelMismatch(CassetteMiss):
    """Replay mode and the request is recorded, but for another model than the profile uses now (a model
    switch): re-record instead of replaying another model's answers."""


def model_id(identity: str | None) -> str | None:
    """The model id of a ``base_url|model`` identity (an identity without ``|`` is the model id itself)."""
    if identity is None:
        return None
    return identity.rsplit("|", 1)[-1]


def server_of(identity: str | None) -> str | None:
    """The base URL of a ``base_url|model`` identity, or None."""
    if identity is None or "|" not in identity:
        return None
    return identity.rsplit("|", 1)[0]


def cassette_key(request: str, model: str | None) -> str:
    """The entry key: the request digest (:func:`request_key`) together with the model id."""
    canonical = json.dumps({"model": model, "request": request}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalise(text: str) -> str:
    """Replace every datamarking token and run canary in ``text`` by fixed placeholders."""
    for token in set(_TOKEN.findall(text)):
        text = text.replace(token, "TOKEN")
    return _CANARY.sub("CANARY", text)


def request_key(
    system: str,
    user: str,
    json_schema: dict[str, Any] | None,
    profile: str,
    images: Sequence[str] = (),
) -> str:
    """The model-independent request digest (the whole key of a format 1 cassette). ``images`` are the sha256
    digests of the attached images (spec 021): part of the digest only when there are some, so a text request
    keeps its key. The image bytes themselves are never recorded."""
    # the system prompt's datamark notice names the token of the user prompt: normalise both together
    tokens = set(_TOKEN.findall(user)) | set(_TOKEN.findall(system))
    for token in tokens:
        system, user = system.replace(token, "TOKEN"), user.replace(token, "TOKEN")
    system, user = _CANARY.sub("CANARY", system), _CANARY.sub("CANARY", user)
    payload: dict[str, Any] = {"system": system, "user": user, "schema": json_schema, "profile": profile}
    if images:
        payload["images"] = list(images)
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
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
        self._by_request: dict[str, set[str | None]] = {}  # request digest -> recorded model ids
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("format") == 1:
                raise ValueError(
                    f"{path}: format 1 cassette (key without the model id); re-key it offline with "
                    f"python -m pkpipe.evals.migrate_cassettes {path}"
                )
            if data.get("format") != FORMAT:
                raise ValueError(f"{path}: unsupported cassette format {data.get('format')!r}")
            self._entries = {e["key"]: e for e in data.get("entries", [])}
            for e in self._entries.values():
                self._by_request.setdefault(e["request_key"], set()).add(e.get("model"))

    def model_for(self, profile: str) -> str | None:
        """The model id the profile uses now (None when its identity is unknown)."""
        return model_id(self.identities.get(profile))

    def __len__(self) -> int:
        return len(self._entries)

    def complete(
        self,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None = None,
        profile: str = "default",
        images: Sequence[ImageInput] | None = None,
    ) -> dict[str, Any]:
        image_digests = [i.sha256 for i in images or ()]
        digest = request_key(system, user, json_schema, profile, image_digests)
        model = self.model_for(profile)
        key = cassette_key(digest, model)
        with self._lock:
            self.used.add(key)
            entry = self._entries.get(key) if self.mode != "record" else None
            if entry is not None:
                self.hits += 1
            others = sorted((m for m in self._by_request.get(digest, set()) if m != model), key=str)
        if entry is not None:
            return self._play(entry)
        if self.mode == "replay":
            with self._lock:
                self.misses += 1
            if others:
                raise CassetteModelMismatch(
                    f"{self.path.name}: this request (profile '{profile}') is recorded for model "
                    f"{', '.join(repr(m) for m in others)}, but the profile now uses {model!r}; replaying "
                    "another model's answers is refused. Re-record with: pkpipe eval --cassette record"
                )
            raise CassetteMiss(
                f"no recording in {self.path.name} for this request (profile '{profile}', model {model!r}); "
                "re-record with: pkpipe eval --cassette record"
            )
        assert self.inner is not None
        identity = self.identities.get(profile)
        entry = {
            "key": key,
            "request_key": digest,
            "profile": profile,
            "model": model,
            "server": server_of(identity),
            "request": {"system": normalise(system), "user": normalise(user), "json_schema": json_schema},
        }
        if image_digests:
            entry["request"]["images"] = image_digests  # digests only, never the bytes
        try:
            if images:
                reply = self.inner.complete(system, user, json_schema, profile=profile, images=images)
            else:
                reply = self.inner.complete(system, user, json_schema, profile=profile)
            entry["response"] = reply
        except ModelError as exc:
            if not isinstance(exc, SchemaError | ContextOverflow):
                # an unreachable server or a refused request (HTTP 4xx such as an unknown model id) is a
                # problem of the endpoint or the profile, not an answer of the model: never a recording
                raise
            entry["error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
        with self._lock:
            self._entries[key] = entry
            self._by_request.setdefault(digest, set()).add(model)
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
                self._by_request = {}
                for e in entries.values():
                    self._by_request.setdefault(e["request_key"], set()).add(e.get("model"))
            if not self._dirty:
                return False
            _write(self.path, entries)
            self._dirty = False
            return True

    def keys(self) -> set[str]:
        with self._lock:
            return set(self._entries)


def _write(path: Path, entries: dict[str, dict[str, Any]]) -> None:
    """Write a model cassette: sorted by key, so the diff is stable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"format": FORMAT, "entries": [entries[k] for k in sorted(entries)]}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


class MigrationError(ValueError):
    """A format 1 cassette cannot be re-keyed deterministically (see :func:`migrate_v1`)."""


def migrate_v1(path: Path, *, models: dict[str, str] | None = None) -> int | None:
    """Re-key a format 1 model cassette to format 2 offline, without calling any model. Returns the number of
    entries, or None when the file is already format 2 (nothing to do).

    Every entry is verified first: its stored (normalised) request must hash back to its old key, so the old
    key maps 1:1 to one request, and the model id comes from the entry's recorded identity
    (``base_url|model``). ``models`` (profile -> model id) names the model only for entries recorded without
    an identity; an entry with neither, a key that does not verify, or two entries landing on one new key
    raise :class:`MigrationError` and leave the file untouched."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") == FORMAT:
        return None
    if data.get("format") != 1:
        raise MigrationError(f"{path}: not a format 1 model cassette (format {data.get('format')!r})")
    out: dict[str, dict[str, Any]] = {}
    for e in data.get("entries", []):
        req = e["request"]
        profile = e.get("profile", "default")
        digest = request_key(req["system"], req["user"], req.get("json_schema"), profile)
        if digest != e["key"]:
            raise MigrationError(f"{path}: entry {e['key'][:12]} does not hash back to its key; re-record it")
        identity = e.get("model")
        model = model_id(identity) if identity else (models or {}).get(profile)
        if model is None:
            raise MigrationError(
                f"{path}: entry {e['key'][:12]} (profile '{profile}') has no recorded model; "
                f"name it with --model {profile}=<model id>"
            )
        key = cassette_key(digest, model)
        if key in out:
            raise MigrationError(f"{path}: two entries for one request and model ({digest[:12]})")
        rest = {k: v for k, v in e.items() if k not in ("key", "profile", "model", "request")}
        out[key] = {
            "key": key,
            "request_key": digest,
            "profile": profile,
            "model": model,
            "server": server_of(identity),
            "request": req,
            **rest,
        }
    _write(path, out)
    return len(out)


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
            if data.get("format") != CLASSIFIER_FORMAT:
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
                "format": CLASSIFIER_FORMAT,
                "classifier": self.identity,
                "entries": [entries[k] for k in sorted(entries)],
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(self.path)
            self._dirty = False
            return True
