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
    #: ``note`` (one INBOX note per item) or an entry kind of the list file (spec 020)
    kind: str = "note"

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
    state: ItemState,
    suggested: str,
    confirm: bool,
    override: str | None,
    valid_categories: frozenset[str] | set[str],
    needs_owner: bool = False,
) -> bool:
    """An open item whose AI suggestion names a category that is no longer configured, and on which the owner
    has not decided yet, is flagged for re-triage (the category was removed from the config).

    An item the owner already confirmed is never re-suggested: the confirm tick belongs to the old suggestion
    and would otherwise release a category the owner never saw (constitution VI). See :func:`needs_decision`.
    An item that ``needs_owner`` (an image: the model reads no pixels, spec 020) has no suggestion to redo.
    """
    if state not in (ItemState.SUGGESTED, ItemState.PENDING) or confirm or override or needs_owner:
        return False
    return suggested not in valid_categories


def needs_decision(
    state: ItemState,
    suggested: str,
    confirm: bool,
    override: str | None,
    valid_categories: frozenset[str] | set[str],
) -> bool:
    """An open item the owner decided on, whose effective category is not configured (removed category or an
    unknown override). It is never released; the owner has to write a configured category into override."""
    if state not in (ItemState.SUGGESTED, ItemState.PENDING) or not (confirm or override):
        return False
    return (override or suggested) not in valid_categories


def is_unreviewed(state: ItemState, confirm: bool, override: str | None) -> bool:
    """Waiting for the owner: not finished, not confirmed, not overridden."""
    return state in (ItemState.SUGGESTED, ItemState.PENDING) and not confirm and not override


class OwnerDecisionError(ValueError):
    """An owner decision (confirm, override, quarantine release) the rules do not allow."""


#: states in which the owner may still confirm or override an item
DECIDABLE = frozenset({ItemState.SUGGESTED, ItemState.PENDING, ItemState.RELEASED})


def check_owner_decision(
    state: ItemState,
    *,
    confirm: bool | None = None,
    override: str | None = None,
    categories: frozenset[str] | set[str] | None = None,
) -> None:
    """Raise :class:`OwnerDecisionError` unless the owner may confirm or override an item in ``state``.

    Used by the owner channels (UI, app MCP; spec 014 FR-002). Quarantined and processed items cannot be
    confirmed (a quarantined item first needs :func:`check_quarantine_release`), and an override must name
    a configured category (constitution V: code decides what is valid).
    """
    if state not in DECIDABLE:
        raise OwnerDecisionError(f"an item in state '{state.value}' cannot be confirmed or overridden")
    if override is not None and override.strip():
        if categories is not None and override.strip() not in categories:
            raise OwnerDecisionError(f"unknown category '{override.strip()}'")
    elif confirm is None:
        raise OwnerDecisionError("nothing to decide: give confirm or an override category")


def check_quarantine_release(state: ItemState) -> None:
    """Only a quarantined item can be released from quarantine (back to ``pending`` for a new decision)."""
    if state is not ItemState.QUARANTINED:
        raise OwnerDecisionError(f"item is '{state.value}', not quarantined")
