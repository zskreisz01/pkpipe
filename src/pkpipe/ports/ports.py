from __future__ import annotations

from typing import Any, Protocol


class ModelPort(Protocol):
    def complete(self, system: str, user: str, json_schema: dict[str, Any] | None = None) -> dict[str, Any]: ...


class ToolPort(Protocol):
    def list_tools(self, allow: list[str]) -> list[str]: ...

    def call(self, tool: str, arguments: dict[str, Any]) -> Any: ...


class NotifyPort(Protocol):
    def send(self, channel: str, message: str, actions: list[dict[str, str]] | None = None) -> None: ...


class StatePort(Protocol):
    def get_item(self, item_id: str) -> dict[str, Any] | None: ...

    def save_item(self, item: dict[str, Any]) -> None: ...


class ConfigPort(Protocol):
    def load_layers(self) -> list[dict[str, Any]]: ...

    def version(self) -> str: ...
