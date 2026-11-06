"""Spec 014 FR-002: the domain rules of owner decisions (confirm, override, quarantine release)."""

from __future__ import annotations

import pytest

from pkpipe.domain.triage import (
    ItemState,
    OwnerDecisionError,
    check_owner_decision,
    check_quarantine_release,
)

CATS = {"tool", "misc"}


@pytest.mark.parametrize("state", [ItemState.SUGGESTED, ItemState.PENDING, ItemState.RELEASED])
def test_open_items_can_be_confirmed_or_overridden(state):
    check_owner_decision(state, confirm=True, categories=CATS)
    check_owner_decision(state, override="tool", categories=CATS)


@pytest.mark.parametrize("state", [ItemState.PROCESSED, ItemState.QUARANTINED])
def test_final_or_quarantined_items_cannot_be_decided(state):
    with pytest.raises(OwnerDecisionError, match="cannot be confirmed"):
        check_owner_decision(state, confirm=True, categories=CATS)
    with pytest.raises(OwnerDecisionError):
        check_owner_decision(state, override="tool", categories=CATS)


def test_override_must_be_a_known_category_and_a_decision_is_required():
    with pytest.raises(OwnerDecisionError, match="unknown category"):
        check_owner_decision(ItemState.PENDING, override="<script>alert(1)</script>", categories=CATS)
    with pytest.raises(OwnerDecisionError, match="unknown category"):
        check_owner_decision(ItemState.PENDING, override="x", categories=CATS)  # a slug needs 2 characters
    # spec 023: a valid new-category name is accepted and stored as its slug (the next release proposes it)
    assert check_owner_decision(ItemState.PENDING, override="Children Books", categories=CATS) == "children-books"
    assert check_owner_decision(ItemState.PENDING, override=" Tool ", categories=CATS) == "tool"
    with pytest.raises(OwnerDecisionError, match="nothing to decide"):
        check_owner_decision(ItemState.PENDING, categories=CATS)
    check_owner_decision(ItemState.PENDING, override="  ", confirm=False, categories=CATS)  # clears override


def test_only_quarantined_items_can_be_released_from_quarantine():
    check_quarantine_release(ItemState.QUARANTINED)
    for state in (ItemState.SUGGESTED, ItemState.PENDING, ItemState.RELEASED, ItemState.PROCESSED):
        with pytest.raises(OwnerDecisionError, match="not quarantined"):
            check_quarantine_release(state)
