"""Spec 027 FR-001/FR-002: the one way out of ``processed`` is the owner's retry guard."""

from __future__ import annotations

import itertools

import pytest

from pkpipe.domain.triage import (
    DEFAULT_MAX_RETRIES,
    TRANSITIONS,
    ItemState,
    OwnerDecisionError,
    check_retry,
    check_transition,
)


def test_a_processed_item_with_a_failed_result_may_be_retried():
    check_retry(ItemState.PROCESSED, processing_failed=True)
    check_retry(ItemState.PROCESSED, processing_failed=True, retries=DEFAULT_MAX_RETRIES - 1)


def test_a_finished_item_is_final():
    with pytest.raises(OwnerDecisionError, match="did not fail"):
        check_retry(ItemState.PROCESSED, processing_failed=False)


@pytest.mark.parametrize(
    "state",
    [ItemState.SUGGESTED, ItemState.PENDING, ItemState.RELEASED, ItemState.AWAITING_CATEGORY],
)
def test_only_a_processed_item_can_be_retried(state):
    with pytest.raises(OwnerDecisionError, match="only a processed item"):
        check_retry(state, processing_failed=True)


def test_a_quarantined_item_is_never_retryable_even_with_a_failed_line():
    with pytest.raises(OwnerDecisionError, match="quarantine"):
        check_retry(ItemState.QUARANTINED, processing_failed=True)


def test_the_owners_retries_are_bounded():
    with pytest.raises(OwnerDecisionError, match="limit 3"):
        check_retry(ItemState.PROCESSED, processing_failed=True, retries=3, max_retries=3)
    check_retry(ItemState.PROCESSED, processing_failed=True, retries=3, max_retries=4)


@pytest.mark.parametrize("new", list(ItemState))
@pytest.mark.parametrize("owner_decided", [True, False])
def test_no_pipeline_path_leaves_processed_or_quarantined(new, owner_decided):
    """The retry is not a transition of the state machine: pipeline code (and so every vault tool) still
    cannot leave ``processed`` or ``quarantined`` for any other state."""
    for old in (ItemState.PROCESSED, ItemState.QUARANTINED):
        reason = check_transition(old, new, owner_decided=owner_decided)
        assert (reason is None) == (new is old), (old, new)
    assert TRANSITIONS[ItemState.PROCESSED] == frozenset({ItemState.PROCESSED})


def test_the_retry_guard_opens_exactly_one_state_pair():
    opened = [
        (old, ItemState.RELEASED)
        for old, failed in itertools.product(ItemState, (True, False))
        if _retryable(old, failed)
    ]
    assert opened == [(ItemState.PROCESSED, ItemState.RELEASED)]


def _retryable(state, failed):
    try:
        check_retry(state, processing_failed=failed)
    except OwnerDecisionError:
        return False
    return True
