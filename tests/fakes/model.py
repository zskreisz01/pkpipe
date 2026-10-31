from __future__ import annotations

from collections.abc import Callable
from typing import Any


class FakeModel:
    """ModelPort fake: scripted replies (queue, or a callable) and a record of every call."""

    def __init__(self, replies: list[dict[str, Any]] | Callable[[str, str], dict[str, Any]] | None = None):
        self._replies = replies if replies is not None else []
        self.calls: list[dict[str, Any]] = []

    def complete(self, system: str, user: str, json_schema: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user, "json_schema": json_schema})
        if callable(self._replies):
            return self._replies(system, user)
        if not self._replies:
            raise AssertionError("FakeModel has no scripted reply left")
        return self._replies.pop(0)
