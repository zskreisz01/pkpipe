from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


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


@runtime_checkable
class ModelPort(Protocol):
    """Structured completions. ``json_schema=None`` returns ``{"text": <reply>}``; otherwise the validated
    JSON object. ``profile`` names an entry of models.json. Raises :class:`ModelError` subclasses."""

    def complete(
        self,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None = None,
        profile: str = "default",
    ) -> dict[str, Any]: ...


@runtime_checkable
class ToolPort(Protocol):
    def list_tools(self, allow: list[str]) -> list[str]: ...

    def call(self, tool: str, arguments: dict[str, Any]) -> Any: ...


@runtime_checkable
class NotifyPort(Protocol):
    def send(self, channel: str, message: str, actions: list[dict[str, str]] | None = None) -> None: ...


@runtime_checkable
class StatePort(Protocol):
    def get_item(self, item_id: str) -> dict[str, Any] | None: ...

    def save_item(self, item: dict[str, Any]) -> None: ...


@runtime_checkable
class ConfigPort(Protocol):
    def load_layers(self) -> list[dict[str, Any]]: ...

    def version(self) -> str: ...
