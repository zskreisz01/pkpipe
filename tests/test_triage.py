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
