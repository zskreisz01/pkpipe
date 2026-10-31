from __future__ import annotations

import copy
from typing import Any


class InMemoryState:
    """StatePort fake. Stores copies so callers cannot mutate persisted state by accident."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        item = self.items.get(item_id)
        return copy.deepcopy(item) if item is not None else None

    def save_item(self, item: dict[str, Any]) -> None:
        self.items[item["id"]] = copy.deepcopy(item)
