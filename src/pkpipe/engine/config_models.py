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
        "handle journal data must not call it (spec 015 FR-002, constitution VII)",
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
CHANNEL_TYPES = ("discord_webhook", "teams_webhook", "apprise", "log")


class Channel(_Strict):
    """One notification target. Secrets (webhook URLs, Apprise URLs) are ``env:NAME`` references."""

    type: str = Field(description="discord_webhook | teams_webhook | apprise | log")
    url: str | None = Field(
        default=None, description="Webhook URL (discord_webhook, teams_webhook): env:NAME"
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
    para_path: str
    triage_hint: str
    sessions: list[Session] = Field(default_factory=list)
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
    quarantine_dir: str


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
