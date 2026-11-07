from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pkpipe.domain.system import SystemSnapshot
    from pkpipe.domain.vault_snapshot import RestorePlan, SnapshotInfo


class ModelError(Exception):
    """Base class of every typed ModelPort failure."""


class SchemaError(ModelError):
    """The model did not produce JSON valid against the requested schema (after any configured retries)."""

    def __init__(self, message: str, raw: str | None = None):
        super().__init__(message)
        self.raw = raw


class ModelUnavailable(ModelError):
    """The model endpoint could not be reached or failed (connection, timeout, 5xx) and no fallback worked."""


class ContextOverflow(ModelError):
    """The prompt does not fit the model context window. Never truncated silently."""


class ImagesNotSupported(ModelError):
    """Images were sent to a model profile that does not declare ``vision: true`` (spec 021 FR-003). Stable
    error: nothing was sent, and a fallback profile without ``vision`` is refused the same way."""


@dataclass(frozen=True)
class ImageInput:
    """One image for a vision model (spec 021): the bytes (normally the JPEG made by ``image.prepare``) and
    their media type. The bytes are private content: they are never part of ``repr``, a log line or a span."""

    data: bytes = field(repr=False)
    media_type: str = "image/jpeg"
    #: A stable identity of the image for recordings (eval cassettes): the same rendering recipe gives the
    #: same ``ref`` on every machine, while the encoded bytes can differ with the imaging library. ``None``:
    #: the digest of the bytes is the identity.
    ref: str | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def key(self) -> str:
        """What a cassette keys the image by: ``ref`` when given, else the digest of the bytes."""
        return self.ref or self.sha256

    def __repr__(self) -> str:
        return f"ImageInput({self.media_type}, {len(self.data)} bytes)"


class ImageError(Exception):
    """An image cannot be prepared. ``code`` is stable: ``pillow_missing``, ``decode_failed``,
    ``too_many_pixels``, ``empty``; the message never contains image content or a file name."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class PreparedImage:
    """The JPEG a vision model gets. ``data`` is private content: never in ``repr``, logs or spans."""

    data: bytes = field(repr=False)
    sha256: str
    width: int
    height: int
    source_format: str
    source_bytes: int

    def __repr__(self) -> str:
        return f"PreparedImage({self.width}x{self.height}, {len(self.data)} bytes, sha256={self.sha256[:12]})"


@runtime_checkable
class ImagePreparePort(Protocol):
    """Decodes an untrusted image and returns the metadata-free JPEG a vision model gets (spec 021 FR-005):
    decoded under a pixel limit (a decompression bomb is refused from its header), EXIF orientation applied
    and all metadata dropped, scaled so the longest side is at most ``max_side`` (never enlarged),
    re-encoded; ``sha256`` is the digest of the prepared bytes. Raises :class:`ImageError`."""

    def prepare(self, data: bytes, *, max_side: int, max_pixels: int) -> PreparedImage: ...


@runtime_checkable
class ModelPort(Protocol):
    """Structured completions. ``json_schema=None`` returns ``{"text": <reply>}``; otherwise the validated
    JSON object. ``profile`` names an entry of models.json. Raises :class:`ModelError` subclasses.

    ``images`` (spec 021) are attached to the user message; the profile must declare ``vision: true``
    (:class:`ImagesNotSupported` otherwise). Without images nothing changes: callers pass the argument only
    when they have images, so a model that predates it keeps working."""

    def complete(
        self,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None = None,
        profile: str = "default",
        images: Sequence[ImageInput] | None = None,
    ) -> dict[str, Any]: ...


class ClassifierUnavailable(Exception):
    """The injection classifier (model files, runtime) is not usable; callers fail closed."""


@runtime_checkable
class ImageStorePort(Protocol):
    """Description cache and timing table of image input (spec 021 FR-012, FR-014), in the app DB.

    The cache key is ``(sha256 of the prepared bytes, model, prompt_version)``; the value is the validated
    description (JSON). Timings are the seconds a model call took per image, the seed of the next run's
    estimate: ``recent_seconds`` returns the last ``limit`` rows of the model, newest first. Neither holds
    image bytes."""

    def cache_get(self, sha256: str, model: str, prompt_version: str) -> dict[str, Any] | None: ...

    def cache_put(
        self, sha256: str, model: str, prompt_version: str, description: dict[str, Any]
    ) -> None: ...

    def record_timing(self, run_id: str, sha256: str, model: str, seconds: float) -> None: ...

    def recent_seconds(self, model: str, limit: int = 50) -> list[float]: ...


@runtime_checkable
class ProposalAttemptsPort(Protocol):
    """Failed evaluations of new-category proposals (spec 022 FR-009), in the app DB.

    Counted per item and requested category name: ``get`` is 0 for another name (the owner changed the
    override), ``bump`` adds one failed evaluation and returns the new count, ``clear`` forgets the item
    (the proposal succeeded)."""

    def get(self, item_id: str, requested: str) -> int: ...

    def bump(self, item_id: str, requested: str) -> int: ...

    def clear(self, item_id: str) -> None: ...


@runtime_checkable
class ClassifierPort(Protocol):
    """Prompt-injection / jailbreak / harm classifier (spec 008 FR-004, spec 009 FR-002); in the reference
    deployment Qwen3Guard-Gen combined with ProtectAI's DeBERTa injection classifier (docs/decisions/0001).

    ``scan`` splits the text into model-sized chunks and returns ``{"model": str, "max_score": float,
    "chunks": int, "scores": list[float]}`` with scores in [0, 1] (1 = malicious). A safety moderator adds
    ``label`` (``Safe`` / ``Controversial`` / ``Unsafe``) and ``categories`` (for example ``Violent``,
    ``Jailbreak``); a combination adds ``members`` (one result per model). The classifier only reports;
    code decides what is a flag. Raises :class:`ClassifierUnavailable` when the model cannot run."""

    def scan(self, text: str) -> dict[str, Any]: ...


@runtime_checkable
class SecurityLogPort(Protocol):
    """The security log of the flag handler (spec 009 US2): ``malicious_urls.jsonl`` and the deny-list.

    ``log_malicious_url`` appends one ``MaliciousUrlEntry`` row (see :mod:`pkpipe.security.quarantine`).
    ``deny_domain`` adds a domain to the deny-list once; flagging it again increments its counter
    (``{"domain", "added", "count"}``). ``denied_domains`` is what the handler has deny-listed so far (the URL
    filter merges it with the config's deny-list file)."""

    def log_malicious_url(self, entry: dict[str, Any]) -> None: ...

    def deny_domain(self, domain: str, *, day: str) -> dict[str, Any]: ...

    def denied_domains(self) -> frozenset[str]: ...


@runtime_checkable
class ContextStorePort(Protocol):
    """The private config files the context refresh maintains (spec 011): ``contexts/<pack>.md`` (and its
    ``.proposed`` sibling) and the query guard's terms file. Paths are relative to the config overlay and
    limited to ``contexts/`` and ``security/``; anything else raises ``ValueError``. ``read`` returns ``None``
    for a file that does not exist; ``remove`` is a no-op for one that does not."""

    def read(self, relative: str) -> str | None: ...

    def write(self, relative: str, text: str) -> None: ...

    def remove(self, relative: str) -> None: ...


@runtime_checkable
class ToolPort(Protocol):
    def list_tools(self, allow: list[str]) -> list[str]: ...

    def call(self, tool: str, arguments: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class Delivery:
    """Outcome of one event to one channel. ``error`` never contains a webhook URL or other secret."""

    event: str
    channel: str
    ok: bool
    error: str | None = None
    truncated: bool = False


@runtime_checkable
class NotifyPort(Protocol):
    """Route ``event`` to its channels (``channels.json`` routes, or the explicit ``channels``) and deliver
    the rendered message. One failing channel never stops the others; failures are returned, not raised.
    An event without a route returns an empty list."""

    def send(
        self, event: str, payload: dict[str, Any], channels: list[str] | None = None
    ) -> list[Delivery]: ...


@runtime_checkable
class StatePort(Protocol):
    def get_item(self, item_id: str) -> dict[str, Any] | None: ...

    def save_item(self, item: dict[str, Any]) -> None: ...


@runtime_checkable
class ConfigPort(Protocol):
    def load_layers(self) -> list[dict[str, Any]]: ...

    def version(self) -> str: ...


@runtime_checkable
class ClockPort(Protocol):
    """Wall clock for wait steps and deadlines. ``now`` is epoch seconds; fakes advance time on ``sleep``."""

    def now(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


@runtime_checkable
class SpanPort(Protocol):
    """Handle of an open span: attributes, events and an explicit failure mark (handled step errors)."""

    def set(self, key: str, value: Any) -> None: ...

    def event(self, name: str, attributes: dict[str, Any] | None = None) -> None: ...

    def fail(self, error_type: str) -> None: ...


@runtime_checkable
class TracerPort(Protocol):
    """Spans for flows, steps, LLM and tool calls (constitution X). The core only knows this protocol;
    ``pkpipe.adapters.tracer_otel`` maps it onto ``pkpipe.telemetry`` (OpenTelemetry, OTLP).

    Every method except ``capture`` and ``security_flag`` returns a context manager yielding a
    :class:`SpanPort`; an exception leaving it marks the span as failed.
    """

    def flow(
        self, flow_id: str, *, trigger: str, config_version: str, data_classes: tuple[str, ...] = ()
    ): ...

    def item(self, item_id: str, category: str | None = None): ...

    def step(self, step_id: str, bpmn_element: str, *, item_id: str | None, item_category: str | None): ...

    def llm(self, model: str): ...

    def tool(self, tool: str): ...

    def capture(self, **content: str) -> bool: ...

    def security_flag(
        self, detector: str, *, action: str = "flag", reason_code: str | None = None
    ) -> None: ...


@runtime_checkable
class RunStorePort(Protocol):
    """Run records (spec 005 FR-004) and pinned config snapshots, keyed by config version.

    Records are plain dicts with the fields of ``pkpipe.engine.runs.Run``.
    """

    def create_run(self, record: dict[str, Any]) -> None: ...

    def update_run(self, run_id: str, **fields: Any) -> None: ...

    def get_run(self, run_id: str) -> dict[str, Any] | None: ...

    def list_runs(self, *, status: str | None = None, flow_id: str | None = None) -> list[dict[str, Any]]: ...

    def save_snapshot(self, version: str, data: dict[str, Any], assets: dict[str, str]) -> None: ...

    def load_snapshot(self, version: str) -> tuple[dict[str, Any], dict[str, str]] | None: ...


@runtime_checkable
class SystemProbePort(Protocol):
    """Reads the host and LLM server state cheaply and without privileges (docs/operations.md "System
    preflight"). Returns a :class:`pkpipe.domain.system.SystemSnapshot`; a reading that is not available is
    left empty and named in ``probe_errors``, never raised. ``include_llm=False`` skips the LLM server part.
    Process entries carry names only (no arguments, no paths)."""

    def snapshot(self, *, include_llm: bool = True) -> SystemSnapshot: ...


@runtime_checkable
class SnapshotPort(Protocol):
    """Vault snapshots and restore (spec 019). ``take`` saves the files a run can change (scope derived from
    the vault policy) before its first write and raises when it cannot: a run never starts without a way back.
    ``finish`` records what the run left behind and returns ``None`` when the run took no snapshot.
    ``plan_restore`` is the dry run, ``apply_restore`` takes a snapshot of the current state first and never
    deletes (removed files go to the snapshot's trash folder). A path the owner changed after the run is
    skipped unless ``force``. Errors are :class:`pkpipe.domain.vault_snapshot.SnapshotError`; their messages
    never contain a note path or text."""

    def take(self, run_id: str, flow_id: str) -> SnapshotInfo: ...

    def finish(self, run_id: str) -> SnapshotInfo | None: ...

    def plan_restore(self, run_id: str, *, force: bool = False) -> RestorePlan: ...

    def apply_restore(self, run_id: str, *, force: bool = False) -> RestorePlan: ...

    def list(self) -> list[SnapshotInfo]: ...

    def show(self, run_id: str) -> dict[str, Any]: ...

    def prune(self) -> dict[str, Any]: ...

    def summary(self) -> dict[str, Any]: ...
