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
