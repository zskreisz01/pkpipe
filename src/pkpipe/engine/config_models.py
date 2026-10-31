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
    context: int | None = Field(default=None, gt=0)
    fallback: str | None = None


class ModelsConfig(RootModel[dict[str, ModelProfile]]):
    @model_validator(mode="after")
    def _fallbacks_exist(self) -> ModelsConfig:
        for name, profile in self.root.items():
            if profile.fallback is not None and profile.fallback not in self.root:
                raise ValueError(f"model profile '{name}': unknown fallback '{profile.fallback}'")
        return self


# mcp_servers.json ------------------------------------------------------------------------------------------
class McpServer(_Strict):
    transport: Literal["streamable_http", "stdio"]
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    backend: str | None = None
    allow: list[str] = Field(default_factory=list)

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
class Channel(_Strict):
    type: str
    url: str | None = None

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


class ChannelsConfig(_Strict):
    channels: dict[str, Channel] = Field(default_factory=dict)
    routes: dict[str, list[str]] = Field(default_factory=dict)

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
class PromptGuard(_Strict):
    model: str
    threshold: float = Field(ge=0.0, le=1.0)
    chunk_tokens: int = Field(gt=0)


class OutputGuard(_Strict):
    model: str
    block_on: list[str]


class Canary(_Strict):
    rotate: Literal["per_run", "daily", "never"]


class UrlFilter(_Strict):
    block_private_networks: bool = True
    max_bytes: int = Field(gt=0)
    deny_list: str | None = None


class SecurityConfig(_Strict):
    prompt_guard: PromptGuard
    output_guard: OutputGuard
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


# registry --------------------------------------------------------------------------------------------------
#: config file stem -> model. Order is the order used by the CLI.
CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "models": ModelsConfig,
    "mcp_servers": McpServersConfig,
    "channels": ChannelsConfig,
    "schedules": SchedulesConfig,
    "categories": CategoriesConfig,
    "security": SecurityConfig,
    "telemetry": TelemetrySettings,
}


def config_json_schemas() -> dict[str, dict[str, Any]]:
    """JSON Schema (draft 2020-12) per config file stem, generated from the Pydantic models."""
    return {name: model.model_json_schema() for name, model in CONFIG_MODELS.items()}


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
    return errors
