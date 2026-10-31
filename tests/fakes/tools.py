from __future__ import annotations

from collections.abc import Callable
from typing import Any


class FakeTools:
    """ToolPort fake: named tools backed by callables. Enforces the allow list like a real adapter must."""

    def __init__(self, tools: dict[str, Callable[..., Any]] | None = None):
        self.tools = dict(tools or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tools(self, allow: list[str]) -> list[str]:
        return sorted(t for t in self.tools if t in allow)

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        if tool not in self.tools:
            raise KeyError(f"unknown tool '{tool}'")
        self.calls.append((tool, arguments))
        return self.tools[tool](**arguments)
