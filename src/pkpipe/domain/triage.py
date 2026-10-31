"""Triage rules from BPMN v4: only confirmed or overridden items are released at 06:00."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ItemState(StrEnum):
    SUGGESTED = "suggested"
    PENDING = "pending"
    RELEASED = "released"
    PROCESSED = "processed"
    QUARANTINED = "quarantined"


@dataclass
class TriageItem:
    id: str
    resource: str
    ai_suggested_category: str
    confirm: bool = False
    override: str | None = None
    state: ItemState = ItemState.SUGGESTED
    notes: list[str] = field(default_factory=list)

    @property
    def effective_category(self) -> str:
        return self.override or self.ai_suggested_category


def release(items: list[TriageItem]) -> tuple[list[TriageItem], list[TriageItem]]:
    """Split items at the 06:00 run: released (confirmed or overridden) vs. pending backlog."""
    released, pending = [], []
    for item in items:
        if item.state in (ItemState.PROCESSED, ItemState.QUARANTINED):
            continue
        if item.confirm or item.override:
            item.state = ItemState.RELEASED
            released.append(item)
        else:
            item.state = ItemState.PENDING
            pending.append(item)
    return released, pending
