from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ModelPort(Protocol):
    def complete(
        self, system: str, user: str, json_schema: dict[str, Any] | None = None
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
