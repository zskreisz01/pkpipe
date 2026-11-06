"""Hand edits in Obsidian leave stray blanks in the confirm checkbox; they must not fail the whole file."""

import pytest

from pkpipe.obsidian.models import TriageRow

BASE = {
    "item": "a1",
    "resource": "https://x.example",
    "suggested": "tool",
    "override": "",
    "state": "suggested",
}


@pytest.mark.parametrize("mark", ["[x]", "[X]", "[ x]", "[x ]", " [ x ] ", "[  x ]"])
def test_stray_blanks_around_the_x_still_mean_confirmed(mark):
    assert TriageRow.from_cells({**BASE, "confirm": mark}).confirm is True


@pytest.mark.parametrize("mark", ["[ ]", "[  ]", "[]", ""])
def test_blank_boxes_mean_not_confirmed(mark):
    assert TriageRow.from_cells({**BASE, "confirm": mark}).confirm is False


@pytest.mark.parametrize("mark", ["[?]", "[xx]", "[-]", "x"])
def test_anything_else_is_still_refused(mark):
    with pytest.raises(ValueError, match="bad checkbox"):
        TriageRow.from_cells({**BASE, "confirm": mark})
