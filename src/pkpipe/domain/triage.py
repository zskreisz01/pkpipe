"""Triage rules from BPMN v4: only confirmed or overridden items are released at 06:00."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum


def item_id_for(resource: str) -> str:
    """Stable triage item id: hash of the resource (the INBOX note path or URL)."""
    return hashlib.sha256(resource.strip().encode("utf-8")).hexdigest()[:12]


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


def needs_retriage(
    state: ItemState, suggested: str, override: str | None, valid_categories: frozenset[str] | set[str]
) -> bool:
    """An unfinished item whose AI suggestion names a category that is no longer configured, and which has
    no valid owner override, is flagged for re-triage (the category was removed from the config)."""
    if state not in (ItemState.SUGGESTED, ItemState.PENDING):
        return False
    if override and override in valid_categories:
        return False
    return not override and suggested not in valid_categories


def is_unreviewed(state: ItemState, confirm: bool, override: str | None) -> bool:
    """Waiting for the owner: not finished, not confirmed, not overridden."""
    return state in (ItemState.SUGGESTED, ItemState.PENDING) and not confirm and not override
