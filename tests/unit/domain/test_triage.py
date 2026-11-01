from pkpipe.domain.triage import ItemState, TriageItem, release


def test_only_confirmed_or_overridden_items_are_released():
    items = [
        TriageItem("1", "a", "tool", confirm=True),
        TriageItem("2", "b", "tool", override="marketing"),
        TriageItem("3", "c", "misc"),
    ]
    released, pending = release(items)
    assert [i.id for i in released] == ["1", "2"]
    assert [i.id for i in pending] == ["3"]
    assert pending[0].state is ItemState.PENDING


def test_override_wins_over_suggestion():
    assert TriageItem("1", "a", "tool", override="job").effective_category == "job"


def test_quarantined_items_are_never_released():
    item = TriageItem("1", "a", "tool", confirm=True, state=ItemState.QUARANTINED)
    released, pending = release([item])
    assert released == [] and pending == []


def test_transitions_release_only_after_owner_decision():
    """Constitution V/VI: code rejects releasing an item the owner has not confirmed or overridden."""
    from pkpipe.domain.triage import check_transition

    assert check_transition(ItemState.SUGGESTED, ItemState.RELEASED, owner_decided=False)
    assert check_transition(ItemState.PENDING, ItemState.RELEASED, owner_decided=False)
    assert check_transition(ItemState.SUGGESTED, ItemState.RELEASED, owner_decided=True) is None
    assert check_transition(ItemState.RELEASED, ItemState.PROCESSED, owner_decided=True) is None


def test_transitions_quarantine_and_processed_are_not_left_by_code():
    from pkpipe.domain.triage import TRANSITIONS, check_transition

    for target in ItemState:
        if target is not ItemState.QUARANTINED:
            assert check_transition(ItemState.QUARANTINED, target, owner_decided=True)
        if target is not ItemState.PROCESSED:
            assert check_transition(ItemState.PROCESSED, target, owner_decided=True)
    assert check_transition(ItemState.PENDING, ItemState.PROCESSED, owner_decided=True)
    assert set(TRANSITIONS) == set(ItemState)


def test_release_rule_output_is_always_an_allowed_transition():
    """The 06:00 release rule and the transition table agree, so its result can be written back."""
    from pkpipe.domain.triage import check_transition

    for state in (ItemState.SUGGESTED, ItemState.PENDING, ItemState.RELEASED):
        for confirm, override in ((False, None), (True, None), (False, "tool")):
            item = TriageItem("i", "r", "misc", confirm, override, state)
            release([item])
            assert check_transition(state, item.state, owner_decided=confirm or bool(override)) is None


def test_needs_retriage_only_for_open_undecided_items_with_a_removed_suggested_category():
    """Spec 007 T005 edge case: category removed from config while pending -> flagged for re-triage."""
    from pkpipe.domain.triage import needs_retriage

    valid = {"tool", "misc"}
    assert needs_retriage(ItemState.PENDING, "gone", False, None, valid)
    assert needs_retriage(ItemState.SUGGESTED, "gone", False, None, valid)
    assert not needs_retriage(ItemState.PENDING, "tool", False, None, valid)
    assert not needs_retriage(ItemState.PENDING, "gone", False, "misc", valid)  # a valid override decides
    assert not needs_retriage(ItemState.PENDING, "gone", False, "also-gone", valid)  # owner's choice
    # constitution VI: the confirm tick was given for the old suggestion; a new one must not inherit it
    assert not needs_retriage(ItemState.PENDING, "gone", True, None, valid)
    for final in (ItemState.PROCESSED, ItemState.QUARANTINED, ItemState.RELEASED):
        assert not needs_retriage(final, "gone", False, None, valid)


def test_needs_decision_for_decided_items_on_an_unconfigured_category():
    """Spec 007 edge case: a confirmed removed category or an unknown override goes back to the owner."""
    from pkpipe.domain.triage import needs_decision

    valid = {"tool", "misc"}
    assert needs_decision(ItemState.PENDING, "gone", True, None, valid)
    assert needs_decision(ItemState.SUGGESTED, "tool", False, "no-such", valid)
    assert needs_decision(ItemState.PENDING, "tool", True, "no-such", valid)  # override wins, and is unknown
    assert not needs_decision(ItemState.PENDING, "gone", True, "misc", valid)
    assert not needs_decision(ItemState.PENDING, "tool", True, None, valid)
    assert not needs_decision(ItemState.PENDING, "gone", False, None, valid)  # undecided: re-triage instead
    for final in (ItemState.PROCESSED, ItemState.QUARANTINED, ItemState.RELEASED):
        assert not needs_decision(final, "gone", True, None, valid)


def test_is_unreviewed_means_waiting_for_the_owner():
    from pkpipe.domain.triage import is_unreviewed

    assert is_unreviewed(ItemState.PENDING, False, None)
    assert not is_unreviewed(ItemState.PENDING, True, None)
    assert not is_unreviewed(ItemState.SUGGESTED, False, "misc")
    assert not is_unreviewed(ItemState.PROCESSED, False, None)
