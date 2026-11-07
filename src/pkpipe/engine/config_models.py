"""Pydantic models for every config file of a pack or overlay (spec 001, T003).

The loader, the CLI (`pkpipe validate`) and the config UI validate with the same models; JSON Schemas are
exported with :func:`config_json_schemas`. Secrets are never stored in config: values like
``env:NAME`` are references resolved at runtime.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    ValidationError,
    field_validator,
    model_validator,
)

ENV_REF = re.compile(r"^env:[A-Za-z_][A-Za-z0-9_]*$")
_CRON_FIELD = re.compile(r"^[\d*/,\-]+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# models.json -----------------------------------------------------------------------------------------------
class ModelProfile(_Strict):
    base_url: str
    model: str
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
    context: int | None = Field(default=None, gt=0)
    timeout_s: int = Field(default=120, gt=0)
    schema_retries: int = Field(default=1, ge=0, le=5)
    retries: int = Field(default=0, ge=0, le=5)
    retry_backoff_s: float = Field(default=2.0, ge=0.0, le=60.0)
    api_key: str | None = None
    fallback: str | None = None
    vision: bool = Field(
        default=False,
        description="The model reads images (spec 021): only a profile with vision: true receives images; "
        "the others refuse them with a stable error",
    )


class ModelsConfig(RootModel[dict[str, ModelProfile]]):
    @model_validator(mode="after")
    def _fallbacks_exist(self) -> ModelsConfig:
        for name, profile in self.root.items():
            if profile.fallback is not None and profile.fallback not in self.root:
                raise ValueError(f"model profile '{name}': unknown fallback '{profile.fallback}'")
            seen, cur = {name}, profile.fallback
            while cur is not None:
                if cur in seen:
                    raise ValueError(f"model profile '{name}': fallback cycle through '{cur}'")
                seen.add(cur)
                cur = self.root[cur].fallback
        return self


# mcp_servers.json ------------------------------------------------------------------------------------------
class McpServer(_Strict):
    transport: Literal["streamable_http", "stdio"]
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    backend: str | None = Field(
        default=None, description="learning server only: open_notebook | notebooklm_browser | fake"
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="learning server only: gateway settings (timeout_s, data_dir, backend_options)",
    )
    allow: list[str] = Field(default_factory=list)
    egress: bool = Field(
        default=False,
        description="The server sends data off this machine (web search, fetch, hosted backends): flows that "
        "handle journal, context or image data must not call it (spec 015 FR-002, constitution VII)",
    )

    @model_validator(mode="after")
    def _transport_fields(self) -> McpServer:
        if self.transport == "streamable_http" and not self.url:
            raise ValueError("streamable_http transport requires 'url'")
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio transport requires 'command'")
        return self


class McpServersConfig(RootModel[dict[str, McpServer]]):
    pass


# channels.json ---------------------------------------------------------------------------------------------
CHANNEL_TYPES = ("discord_webhook", "discord_bot", "teams_webhook", "apprise", "log")


class Channel(_Strict):
    """One notification target. Secrets (webhook URLs, Apprise URLs) are ``env:NAME`` references."""

    type: str = Field(description="discord_webhook | discord_bot | teams_webhook | apprise | log")
    url: str | None = Field(
        default=None, description="Webhook URL (discord_webhook, teams_webhook): env:NAME"
    )
    token: str | None = Field(default=None, description="Bot token (discord_bot): env:NAME")
    channel_id: str | None = Field(
        default=None, description="Target channel (discord_bot): a numeric id or env:NAME"
    )
    urls: list[str] = Field(
        default_factory=list, description="Apprise service URLs (apprise): each an env:NAME reference"
    )

    @field_validator("url")
    @classmethod
    def _no_inline_secret(cls, v: str | None) -> str | None:
        if (
            v is not None
            and not ENV_REF.match(v)
            and "://" in v
            and "@" in v.split("://", 1)[1].split("/")[0]
        ):
            raise ValueError("credentials must not be inlined in a URL; use env:NAME")
        return v

    @model_validator(mode="after")
    def _type_requirements(self) -> Channel:
        if self.type not in CHANNEL_TYPES:
            raise ValueError(f"unknown channel type '{self.type}' (known: {', '.join(CHANNEL_TYPES)})")
        if self.type in ("discord_webhook", "teams_webhook"):
            if not self.url:
                raise ValueError(f"channel type '{self.type}' needs 'url' (an env:NAME reference)")
            if not ENV_REF.match(self.url):
                raise ValueError("webhook URLs are secrets; use an env:NAME reference, not the URL itself")
        if self.type == "discord_bot":
            if not self.token or not ENV_REF.match(self.token):
                raise ValueError("channel type 'discord_bot' needs 'token' (an env:NAME reference)")
            if not self.channel_id or not (ENV_REF.match(self.channel_id) or self.channel_id.isdigit()):
                raise ValueError("channel type 'discord_bot' needs 'channel_id' (digits or env:NAME)")
        if self.type == "apprise":
            if not self.urls:
                raise ValueError("channel type 'apprise' needs 'urls' (env:NAME references)")
            if not all(ENV_REF.match(u) for u in self.urls):
                raise ValueError("Apprise URLs contain credentials; use env:NAME references")
        return self


class ChannelsConfig(_Strict):
    channels: dict[str, Channel] = Field(default_factory=dict)
    routes: dict[str, list[str]] = Field(default_factory=dict)
    ui_url: str | None = Field(
        default=None, description="Link appended to a message that had to be truncated for its channel"
    )

    @model_validator(mode="after")
    def _routes_reference_channels(self) -> ChannelsConfig:
        for event, targets in self.routes.items():
            for t in targets:
                if t not in self.channels:
                    raise ValueError(f"route '{event}' references unknown channel '{t}'")
        return self


# schedules.json --------------------------------------------------------------------------------------------
class Schedule(_Strict):
    cron: str
    flow: str
    enabled: bool = True
    misfire_grace_s: int = Field(
        default=21600,
        gt=0,
        description="A run missed while asleep still starts once if woken within this time",
    )
    timezone: str | None = Field(
        default=None, description="IANA zone for the cron; default: the machine's zone"
    )

    @field_validator("cron")
    @classmethod
    def _five_field_cron(cls, v: str) -> str:
        parts = v.split()
        if len(parts) != 5 or not all(_CRON_FIELD.match(p) for p in parts):
            raise ValueError(f"not a 5-field cron expression: '{v}'")
        return v


class SchedulesConfig(RootModel[list[Schedule]]):
    @model_validator(mode="after")
    def _unique_flows(self) -> SchedulesConfig:
        flows = [s.flow for s in self.root]
        dup = {f for f in flows if flows.count(f) > 1}
        if dup:
            raise ValueError(f"duplicate schedule flow(s): {sorted(dup)}")
        return self


# categories.json -------------------------------------------------------------------------------------------
class Session(_Strict):
    id: str
    system: str
    instruction: str
    tools: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    context: str | None = None
    output_schema: str | None = None

    @field_validator("system", "instruction", "context", "output_schema")
    @classmethod
    def _relative_reference(cls, v: str | None) -> str | None:
        """References stay inside their layer: no absolute paths, no '..', no backslashes."""
        if v is None:
            return v
        parts = v.split("/")
        if not v or v.startswith("/") or "\\" in v or ":" in v or any(p in ("", ".", "..") for p in parts):
            raise ValueError(f"'{v}' must be a relative path inside the pack (no '..', no absolute path)")
        return v

    @field_validator("tools")
    @classmethod
    def _tool_format(cls, v: list[str]) -> list[str]:
        for t in v:
            if t.count(":") != 1 or not all(t.split(":")):
                raise ValueError(f"tool '{t}' must be '<server>:<tool>'")
        return v


class Category(_Strict):
    id: str
    label: str
    subflow: str
    para_path: str = Field(
        description="PARA folder of the notes; `{resources_dir}`, `{projects_dir}`, `{areas_dir}`, "
        "`{inbox_dir}` and `{archive_dir}` follow `layout.json`"
    )
    triage_hint: str
    sessions: list[Session] = Field(default_factory=list)

    @field_validator("para_path")
    @classmethod
    def _para_placeholders(cls, value: str) -> str:
        allowed = {"inbox_dir", "projects_dir", "areas_dir", "resources_dir", "archive_dir"}
        unknown = sorted(set(re.findall(r"\{([^{}]*)\}", value)) - allowed)
        if unknown:
            raise ValueError(f"unknown placeholder {{{unknown[0]}}} (use {', '.join(sorted(allowed))})")
        return value

    note_session: str | None = Field(
        default=None,
        description="Session whose validated output becomes the stored note (spec 010); default "
        "'synthesize', "
        "else the last session. Later sessions (for example spec 016's system relevance) run after it "
        "without changing the note",
    )
    enabled: bool = True

    @model_validator(mode="after")
    def _unique_sessions(self) -> Category:
        ids = [s.id for s in self.sessions]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"category '{self.id}': duplicate session id(s) {sorted(dup)}")
        return self


class CategoriesConfig(RootModel[list[Category]]):
    pass


# security.json ---------------------------------------------------------------------------------------------
class ClassifierMember(_Strict):
    """One model of the injection / harm classifier (decision record docs/decisions/0001)."""

    backend: Literal["qwen3guard", "deberta"]
    model: str | None = Field(default=None, description="Model name (Hugging Face id), for reports")
    model_dir: str | None = Field(
        default=None,
        description="Local model directory (may be env:NAME|default); default ~/tools/qwen3guard/<model "
        "name> (qwen3guard) or ~/tools/protectai-deberta-pi-v2 (deberta)",
    )
    chunk_tokens: int | None = Field(
        default=None, gt=0, le=4096, description="Tokens per scanned chunk (deberta: at most 512)"
    )

    @model_validator(mode="after")
    def _window(self) -> ClassifierMember:
        if self.backend == "deberta" and self.chunk_tokens is not None and self.chunk_tokens > 512:
            raise ValueError("the deberta classifier has a 512-token window (chunk_tokens <= 512)")
        return self


class ClassifierSettings(_Strict):
    """``security.classifier``: the local classifier of secure fetch (spec 008 FR-004) and of the output
    guard (spec 009 FR-002). ``combined`` scans with every member and takes the maximum score."""

    backend: Literal["qwen3guard", "deberta", "combined"]
    model: str | None = None
    model_dir: str | None = None
    chunk_tokens: int | None = Field(default=None, gt=0, le=4096)
    members: list[ClassifierMember] = Field(default_factory=list)
    threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="Score that raises a flag")
    required: bool = Field(
        default=True,
        description="Fail closed: without a usable classifier every secure fetch and every guarded output "
        "is flagged (spec 008, 009)",
    )

    @model_validator(mode="after")
    def _members(self) -> ClassifierSettings:
        if self.backend == "combined":
            if not self.members:
                raise ValueError("backend 'combined' needs at least one entry in members")
            if self.model_dir or self.model or self.chunk_tokens:
                raise ValueError("backend 'combined' takes model settings per member only")
        else:
            if self.members:
                raise ValueError(f"members are only allowed with backend 'combined', not '{self.backend}'")
            ClassifierMember(
                backend=self.backend,
                model=self.model,
                model_dir=self.model_dir,
                chunk_tokens=self.chunk_tokens,
            )
        return self


class Heuristics(_Strict):
    threshold: float = Field(default=1.0, gt=0.0, description="Heuristic score that raises a flag")


class OutputGuard(_Strict):
    """``security.output_guard`` (spec 009): which safety labels of the classifier count as
    ``malicious_content``. Injection scores use ``security.classifier.threshold``, heuristics
    ``security.heuristics.threshold``; a canary in an output is always a flag."""

    block_on: list[Literal["Unsafe", "Controversial"]] = Field(default_factory=lambda: ["Unsafe"])


class Canary(_Strict):
    rotate: Literal["per_run", "daily", "never"]


class UrlFilter(_Strict):
    block_private_networks: bool = True
    max_bytes: int = Field(gt=0)
    deny_list: str | None = None


class QueryGuard(_Strict):
    """``security.query_guard`` (spec 010 FR-002): terms (client and project names) that no web query of a
    session with tools may contain."""

    blocked_terms: list[str] = Field(default_factory=list)
    terms_file: str | None = Field(
        default=None,
        description="Asset with one blocked term per line (for example contexts/blocked_terms.txt)",
    )


class SecurityConfig(_Strict):
    classifier: ClassifierSettings
    heuristics: Heuristics = Field(default_factory=Heuristics)
    output_guard: OutputGuard = Field(default_factory=OutputGuard)
    query_guard: QueryGuard = Field(default_factory=QueryGuard)
    canary: Canary
    url_filter: UrlFilter
    quarantine_dir: str | None = Field(
        default=None,
        deprecated=True,
        description="Deprecated: the folder is `layout.json` `quarantine_dir`; when set it must equal it",
    )


# telemetry.json --------------------------------------------------------------------------------------------
class CaptureContent(_Strict):
    flows: list[str] = Field(default_factory=list)


class TelemetrySettings(_Strict):
    """Content capture per flow (spec 003 FR-004). Exporters come from ``OTEL_*`` env, never from config.

    Flows handling journal or context data are forced off at runtime whatever is listed here.
    """

    capture_content: CaptureContent = Field(default_factory=CaptureContent)


# evals.json ------------------------------------------------------------------------------------------------
class Threshold(_Strict):
    """One gate condition on a suite metric: ``min`` (value must be >=) or ``max`` (value must be <=)."""

    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Threshold:
        if (self.min is None) == (self.max is None):
            raise ValueError("a threshold has exactly one of 'min' or 'max'")
        return self


class SuiteGate(_Strict):
    gate: dict[str, Threshold] = Field(default_factory=dict, description="metric -> threshold")


class PairwiseSettings(_Strict):
    min_score: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="A/B score of the new instruction (win = 1, tie = 0.5, loss = 0) needed to publish",
    )


class CalibrationSettings(_Strict):
    max_gap: int = Field(default=1, ge=0, le=4, description="Judge/owner difference that counts as disagree")
    max_disagreement_rate: float = Field(default=0.25, ge=0.0, le=1.0)


ThermalLevelName = Literal["nominal", "fair", "serious", "critical"]


class SystemPreflight(_Strict):
    """System preflight thresholds (docs/operations.md "System preflight"): code maps a system snapshot
    (memory, swap, thermal state, load, LLM server) to ok / degraded / blocked, an eval concurrency and a
    timeout multiplier. A *reduce* rule caps concurrency at ``reduced_concurrency``, a *minimal* rule at 1,
    a *block* rule stops the run."""

    enabled: bool = Field(default=True, description="Run the preflight before live evals (`pkpipe eval`)")
    max_concurrency: int = Field(
        default=4, ge=1, le=16, description="Concurrency recommended when everything is ok (live tests)"
    )
    reduced_concurrency: int = Field(default=2, ge=1, le=16)
    reduced_timeout_multiplier: float = Field(default=1.5, ge=1.0, le=10.0)
    minimal_timeout_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    memory_pressure_reduce: Literal["warn", "critical"] = Field(
        default="warn", description="Kernel memory pressure level that reduces concurrency"
    )
    memory_pressure_block: Literal["warn", "critical"] | None = Field(
        default="critical", description="Kernel memory pressure level that blocks (null: never)"
    )
    available_memory_reduce_pct: float = Field(default=10.0, ge=0.0, le=100.0)
    available_memory_block_pct: float = Field(default=3.0, ge=0.0, le=100.0)
    swap_reduce_pct: float = Field(default=80.0, ge=0.0, le=100.0, description="Swap used / total, percent")
    swap_minimal_pct: float = Field(default=95.0, ge=0.0, le=100.0)
    swap_min_gib: float = Field(
        default=12.0,
        ge=0.0,
        description="The swap percentage rules apply only from this much swap used (GiB) or under memory "
        "pressure: macOS resizes its swap file, so a small, nearly full swap file alone is no stall",
    )
    load_per_cpu_reduce: float = Field(default=1.5, gt=0.0, description="1-minute load average per CPU")
    load_per_cpu_minimal: float = Field(default=3.0, gt=0.0)
    thermal_reduce: ThermalLevelName = "fair"
    thermal_minimal: ThermalLevelName = "serious"
    thermal_block: ThermalLevelName | None = "critical"
    llm_busy_queued: int = Field(
        default=1, ge=1, description="Requests queued by other clients on a required model that reduce"
    )
    require_loaded_models: bool = Field(
        default=True, description="Block when the server reports a required model as not loaded"
    )
    sample_interval_s: float = Field(default=30.0, ge=1.0, le=3600.0, description="Live-test sampler period")

    @model_validator(mode="after")
    def _ordered(self) -> SystemPreflight:
        if self.available_memory_block_pct > self.available_memory_reduce_pct:
            raise ValueError("available_memory_block_pct must be <= available_memory_reduce_pct")
        if self.swap_minimal_pct < self.swap_reduce_pct:
            raise ValueError("swap_minimal_pct must be >= swap_reduce_pct")
        if self.load_per_cpu_minimal < self.load_per_cpu_reduce:
            raise ValueError("load_per_cpu_minimal must be >= load_per_cpu_reduce")
        if self.reduced_concurrency > self.max_concurrency:
            raise ValueError("reduced_concurrency must be <= max_concurrency")
        return self


class EvalsConfig(_Strict):
    """Eval gates (spec 013 SC-001). Thresholds only move upwards (constitution IX): a publish that lowers a
    ``min``, raises a ``max`` or drops a gate is refused."""

    judge_profile: str = Field(default="judge", description="models.json profile of the LLM judge")
    suites: dict[str, SuiteGate] = Field(default_factory=dict)
    pairwise: PairwiseSettings = Field(default_factory=PairwiseSettings)
    calibration: CalibrationSettings = Field(default_factory=CalibrationSettings)
    concurrency: int = Field(
        default=1,
        ge=1,
        le=16,
        description="Eval cases (model and judge requests) in flight at once; > 1 needs a model server with "
        "parallel predictions (LM Studio: docs/evaluation.md). `pkpipe eval --concurrency N` overrides it",
    )
    preflight: SystemPreflight = Field(
        default_factory=SystemPreflight,
        description="System preflight thresholds (memory, swap, thermal, load, LLM server); "
        "`pkpipe doctor` shows the decision",
    )


class SnapshotsConfig(_Strict):
    """Vault snapshots (spec 019 FR-006): what is kept of the state before each writing run."""

    retention_days: int = Field(
        default=14,
        ge=1,
        le=3650,
        description="Snapshots older than this are pruned (the newest per flow stays)",
    )
    max_bytes: int = Field(
        default=1024**3,
        ge=1_000_000,
        description="Upper bound of all snapshot copies; the oldest go first (the newest per flow stays)",
    )
    max_file_bytes: int = Field(
        default=5 * 1024**2,
        ge=1024,
        description="A note larger than this is recorded in the manifest but not copied (a restore skips it)",
    )


# images.json -----------------------------------------------------------------------------------------------
class ImagesConfig(_Strict):
    """Image input (spec 021 FR-011, data-model.md ``images.json``): the time budget of the image batch, the
    preparation of an image and the limits of one description call."""

    time_budget_s: int = Field(
        default=7200,
        gt=0,
        description="The image batch of one cycle stops before an image whose expected end (elapsed + "
        "running estimate) reaches this; the rest stays in the INBOX list untouched",
    )
    estimate_s: float = Field(
        default=60.0, gt=0, description="Seconds per image before there is any measurement (first run)"
    )
    per_image_timeout_s: int = Field(
        default=300, gt=0, description="Hard limit for the description of one image; it fails alone"
    )
    max_side: int = Field(default=1024, ge=64, le=4096, description="Longest side after scaling, in pixels")
    max_tokens: int = Field(
        default=1200,
        gt=0,
        description="Upper limit of the answer tokens of a description: the vision profile's max_tokens must "
        "not exceed it",
    )
    max_bytes: int = Field(default=10 * 1024 * 1024, gt=0, description="Largest image file that is read")
    retries: int = Field(
        default=1, ge=0, le=3, description="Repeated description attempts after an invalid answer"
    )
    triage_embed_width: int = Field(
        default=200,
        ge=0,
        le=2000,
        description="Width in pixels of the image the triage file shows for an image row: the resource cell "
        "is the local Obsidian embed ![[name|width]] (never a remote URL); 0 writes the plain name",
    )
    max_pixels: int = Field(
        default=40_000_000,
        gt=0,
        description="Decoded pixel limit (decompression bomb guard, from the header)",
    )


# triage.json / ui.json -------------------------------------------------------------------------------------
class TriageConfig(_Strict):
    """Where the triage decisions are made and kept (spec 023)."""

    surface: Literal["vault", "web"] = Field(
        default="vault",
        description="vault: the triage table is in layout.triage_file (Obsidian, as before). web: the "
        "machine-owned table lives in layout.triage_data_file, decisions are made in the web UI, and "
        "layout.triage_file is a generated summary page with a link and no table. The vault policy file "
        "of the vault server mirrors it as triage_surface (pkpipe validate checks both agree)",
    )
    retry_max: int = Field(
        default=3,
        ge=1,
        le=20,
        description="How often the owner may send one failed item through the release again (spec 027). "
        "The count per item is kept in the app database; at the limit the item has to be fixed or processed "
        "by hand",
    )


class JournalTodosConfig(_Strict):
    """The pkpipe todo block of the daily journal (spec 024)."""

    enabled: bool = Field(
        default=False,
        description="Write the todo lines of the run report into the daily journal. Also needs the vault "
        "policy to grant the journal folder to the tool write_journal_todos (allow.write_journal_todos)",
    )
    heading: str = Field(
        default="## pkpipe todo",
        description="The Markdown heading above the block; the block sits between two HTML comment markers "
        "below it, and nothing else of the journal is ever touched",
    )

    @field_validator("heading")
    @classmethod
    def _heading(cls, value: str) -> str:
        from pkpipe.domain.journal_todos import JournalTodoError, check_heading

        try:
            return check_heading(value)
        except JournalTodoError as exc:
            raise ValueError(str(exc)) from None


class ReportConfig(_Strict):
    """The run report after every triage and release run, and its todos (spec 024)."""

    enabled: bool = Field(
        default=True,
        description="Send the run report as the daily_summary notification after every triage and release "
        "run (counts per category, created evaluations / notebooks / podcasts, todos with counts). "
        "false keeps the earlier one-line messages (triage_ready and the daily summary)",
    )
    tool_category: str = Field(
        default="tool",
        pattern=r"^[a-z0-9][a-z0-9-]{0,39}$",
        description="Category whose notes the owner reads as tool evaluations: the todo counts its notes "
        "that have no `reviewed: true` in the front matter",
    )
    journal_todos: JournalTodosConfig = Field(default_factory=JournalTodosConfig)


class SourceDiscoveryConfig(_Strict):
    """Items without a source URL: find one by web search (spec 025). Off by default: a search query leaves
    the machine, so the owner switches it on knowingly."""

    enabled: bool = Field(
        default=False,
        description="Search for the source of an item that names no URL (an image, a note without a link) "
        "instead of failing it with 'no source URL in the note'. Needs the websearch tool of the category "
        "plan's server and the secure-fetch web-fetch server",
    )
    categories: list[str] = Field(
        default_factory=lambda: ["tool"],
        description="Categories (ids) that use discovery. Only categories whose subflow is other_resource "
        "can: learning-material would send a found URL to the notebook service and is not supported",
    )
    from_images: bool = Field(
        default=True,
        description="Image items: the query is built from the validated image description (topic and the "
        "first words of the visible text). An address in the image wins over a search",
    )
    from_notes: bool = Field(
        default=True, description="Note and text items without a link: query from the text"
    )
    max_candidates: int = Field(
        default=2,
        ge=1,
        le=5,
        description="At most this many found pages are fetched, in order, until one is clean",
    )
    max_results: int = Field(default=5, ge=1, le=10, description="Results asked from the search tool")
    max_query_chars: int = Field(default=120, ge=20, le=300, description="Longest search query (whole words)")
    query_template: str = Field(
        default="{topic} {text}",
        max_length=200,
        description="Placeholders {topic} (image topic, or empty) and {text} (image visible text, or the "
        "text of the note). Nothing else is substituted; no model builds the query",
    )

    @field_validator("categories")
    @classmethod
    def _categories(cls, value: list[str]) -> list[str]:
        import re as _re

        for c in value:
            if not _re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", c):
                raise ValueError(f"category id '{c}' is not a valid id")
        return list(dict.fromkeys(value))

    @field_validator("query_template")
    @classmethod
    def _template(cls, value: str) -> str:
        import string as _string

        names = {f for _, f, _, _ in _string.Formatter().parse(value) if f is not None}
        if not names <= {"topic", "text"} or not names:
            raise ValueError("query_template may use only {topic} and {text}, at least one of them")
        return value


_HOST = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")


class UiConfig(_Strict):
    """How the web UI is reached (spec 023): the address the owner opens, and the Host names it answers to
    besides loopback and its own bind address (Tailscale MagicDNS name and IP)."""

    public_url: str | None = Field(
        default=None,
        description="The address the owner opens the UI at (for example the Tailscale URL); linked from the "
        "generated triage page. http or https, no credentials, no query",
    )
    allowed_hosts: list[str] = Field(
        default_factory=list,
        description="Extra Host header values (name or IP, no port) the UI accepts: the MagicDNS name and "
        "the Tailscale IP. The host of public_url is added automatically",
    )

    @field_validator("public_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from urllib.parse import urlsplit

        parts = urlsplit(value.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("public_url must be an http or https URL with a host name")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("public_url must not contain credentials, a query or a fragment")
        return value.strip().rstrip("/")

    @field_validator("allowed_hosts")
    @classmethod
    def _hosts(cls, values: list[str]) -> list[str]:
        import ipaddress

        out: list[str] = []
        for raw in values:
            host = str(raw).strip().lower()
            try:
                ip = ipaddress.ip_address(host.strip("[]"))
                host = f"[{ip}]" if ip.version == 6 else str(ip)
            except ValueError:
                if not _HOST.match(host) or ".." in host:
                    raise ValueError(
                        f"allowed host '{raw}': give a host name or an IP address (no scheme, port or path)"
                    ) from None
            if host not in out:
                out.append(host)
        return out

    def hosts(self) -> list[str]:
        """``allowed_hosts`` plus the host of ``public_url``, as the Host allowlist wants them."""
        from urllib.parse import urlsplit

        hosts = list(self.allowed_hosts)
        if self.public_url:
            host = (urlsplit(self.public_url).hostname or "").lower()
            host = f"[{host}]" if ":" in host else host
            if host and host not in hosts:
                hosts.append(host)
        return hosts


# registry --------------------------------------------------------------------------------------------------
from pkpipe.domain.layout import VaultLayout  # noqa: E402  (layout.json is validated by the domain model)

#: config file stem -> model. Order is the order used by the CLI.
CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "models": ModelsConfig,
    "mcp_servers": McpServersConfig,
    "channels": ChannelsConfig,
    "schedules": SchedulesConfig,
    "categories": CategoriesConfig,
    "security": SecurityConfig,
    "layout": VaultLayout,
    "snapshots": SnapshotsConfig,
    "images": ImagesConfig,
    "triage": TriageConfig,
    "ui": UiConfig,
    "report": ReportConfig,
    "source_discovery": SourceDiscoveryConfig,
    "telemetry": TelemetrySettings,
    "evals": EvalsConfig,
}


def config_json_schemas() -> dict[str, dict[str, Any]]:
    """JSON Schema (draft 2020-12) per config file stem, generated from the Pydantic models.

    ``flow`` is the schema of one ``flows/<id>.json`` file.
    """
    from pkpipe.engine.flow_models import Flow

    schemas = {name: model.model_json_schema() for name, model in CONFIG_MODELS.items()}
    schemas["flow"] = Flow.model_json_schema(by_alias=True)
    return schemas


def validate_effective(data: dict[str, Any]) -> list[str]:
    """Validate a merged config dict; returns human readable errors (empty list = valid)."""
    errors: list[str] = []
    for name, model in CONFIG_MODELS.items():
        if name not in data:
            continue
        try:
            model.model_validate(data[name])
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"])
                errors.append(f"{name}.json: {loc}: {err['msg']}" if loc else f"{name}.json: {err['msg']}")
    errors += _layout_errors(data)
    errors += _surface_errors(data)
    if "flows" in data:
        from pkpipe.engine.flow_models import FlowsConfig

        try:
            FlowsConfig.model_validate(data["flows"])
        except ValidationError as exc:
            for err in exc.errors():
                loc = list(err["loc"])
                where = f"flows/{loc[0]}.json" if loc else "flows"
                rest = ".".join(str(p) for p in loc[1:])
                errors.append(f"{where}: {rest}: {err['msg']}" if rest else f"{where}: {err['msg']}")
    return errors


def _surface_errors(data: dict[str, Any]) -> list[str]:
    """Spec 023: the ``web`` surface sends the owner to the UI, so the generated page needs its address."""
    triage, ui = data.get("triage"), data.get("ui")
    if not isinstance(triage, dict) or triage.get("surface") != "web":
        return []
    if not isinstance(ui, dict) or not ui.get("public_url"):
        return ["triage.json: surface 'web' needs ui.json public_url (the link the triage page shows)"]
    return []


def _layout_errors(data: dict[str, Any]) -> list[str]:
    """Cross-file checks of the vault layout (spec 020 FR-008): the paths sit where the vault tools may use
    them, and ``security.quarantine_dir`` (deprecated duplicate) agrees with the layout."""
    from pkpipe.domain.layout import layout_from, layout_structure_errors

    try:
        layout = layout_from(data.get("layout"))
    except (ValidationError, TypeError, ValueError):
        return []  # reported by the model validation above
    errors = [f"layout.json: {e}" for e in layout_structure_errors(layout)]
    security = data.get("security")
    legacy = security.get("quarantine_dir") if isinstance(security, dict) else None
    if legacy is not None and str(legacy).rstrip("/") != layout.quarantine_dir:
        errors.append(
            f"security.json: quarantine_dir '{legacy}' differs from layout.json quarantine_dir "
            f"'{layout.quarantine_dir}' (drop the security.json key)"
        )
    return errors
