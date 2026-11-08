from __future__ import annotations

import copy
from typing import Any


class FakeClassifierReview:
    """ClassifierReviewPort fake: items held after a classifier warning, and their acceptance (spec 029)."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def hold(self, item_id: str, url: str, evidence: dict[str, Any]) -> None:
        self.items[item_id] = {
            "item_id": item_id,
            "url": url,
            "evidence": copy.deepcopy(evidence),
            "held_at": "t",
            "waived": False,
        }

    def review_of(self, item_id: str) -> dict[str, Any] | None:
        return copy.deepcopy(self.items.get(item_id))

    def held(self) -> dict[str, dict[str, Any]]:
        return copy.deepcopy(self.items)

    def waive(self, item_id: str) -> None:
        if item_id in self.items:
            self.items[item_id]["waived"] = True

    def waived(self, item_id: str) -> bool:
        return bool(self.items.get(item_id, {}).get("waived"))
