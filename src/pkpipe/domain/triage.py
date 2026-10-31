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


# Allowed state changes made by pipeline code (constitution V/VI). Leaving ``quarantined`` is an owner
# decision and ``processed`` is final, so neither has an outgoing transition here. ``released`` additionally
# requires the owner's confirm tick or override (see :func:`check_transition`).
TRANSITIONS: dict[ItemState, frozenset[ItemState]] = {
    ItemState.SUGGESTED: frozenset(
        {ItemState.SUGGESTED, ItemState.PENDING, ItemState.RELEASED, ItemState.QUARANTINED}
    ),
    ItemState.PENDING: frozenset(
        {ItemState.PENDING, ItemState.SUGGESTED, ItemState.RELEASED, ItemState.QUARANTINED}
    ),
    ItemState.RELEASED: frozenset(
        {ItemState.RELEASED, ItemState.PENDING, ItemState.PROCESSED, ItemState.QUARANTINED}
    ),
    ItemState.PROCESSED: frozenset({ItemState.PROCESSED}),
    ItemState.QUARANTINED: frozenset({ItemState.QUARANTINED}),
}


def check_transition(old: ItemState, new: ItemState, *, owner_decided: bool) -> str | None:
    """Return why ``old -> new`` is not allowed for pipeline code, or ``None`` when it is."""
    if new not in TRANSITIONS[old]:
        return f"state change {old.value} -> {new.value} is not allowed"
    if new is ItemState.RELEASED and old is not ItemState.RELEASED and not owner_decided:
        return "an item is released only after the owner confirms or overrides it"
    return None


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
