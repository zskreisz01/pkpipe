"""Spec 024: the whole write rule of the pkpipe todo block in a daily journal (pure text)."""

from __future__ import annotations

import pytest

from pkpipe.domain.journal_todos import (
    BEGIN,
    DEFAULT_HEADING,
    END,
    JournalTodoError,
    check_heading,
    check_lines,
    replace_block,
)

LINE = "- 2026-11-07 - Review triage decisions - 14 items - [triage UI](http://100.64.0.10:8787/triage)"


def test_a_missing_note_is_created_with_the_journal_front_matter_and_the_section_only():
    text = replace_block(None, DEFAULT_HEADING, [LINE])
    assert text == f"---\ntype: journal\n---\n\n## pkpipe todo\n{BEGIN}\n{LINE}\n{END}\n"


def test_a_note_without_the_section_gets_heading_and_block_at_the_end_and_nothing_else_changes():
    old = "# 2026-11-07\n\n## Ideas\n- an idea\n"
    new = replace_block(old, DEFAULT_HEADING, [LINE])
    assert new.startswith(old) and new[len(old) :] == f"\n## pkpipe todo\n{BEGIN}\n{LINE}\n{END}\n"


def test_a_note_without_a_final_newline_still_ends_cleanly():
    new = replace_block("# day\nlast line", DEFAULT_HEADING, [LINE])
    assert new.startswith("# day\nlast line\n\n## pkpipe todo\n") and new.endswith(f"{END}\n")


def test_an_existing_identical_heading_gets_the_block_below_it_not_a_second_heading():
    old = "# d\n\n## pkpipe todo\n\n## Ideas\n- x\n"
    new = replace_block(old, DEFAULT_HEADING, [LINE])
    assert new.count("## pkpipe todo") == 1
    assert new == f"# d\n\n## pkpipe todo\n{BEGIN}\n{LINE}\n{END}\n\n## Ideas\n- x\n"


def test_writing_the_same_day_again_replaces_the_block_and_never_duplicates():
    first = replace_block("# d\n\n## Ideas\n- x\n", DEFAULT_HEADING, [LINE])
    again = replace_block(first, DEFAULT_HEADING, [LINE])
    assert again == first and again.count(BEGIN) == 1
    other = replace_block(first, DEFAULT_HEADING, ["- 2026-11-07 - Read tool evaluations - 2 items"])
    assert LINE not in other and "Read tool evaluations" in other and other.count(BEGIN) == 1


def test_not_a_byte_outside_the_markers_changes():
    before = "introé \r\n\n## Ideas\n- x\n\n"
    after = "\n\n---\n## Evening close\n- Done today: **bold** `code` [[link]]\n"
    note = f"{before}{BEGIN}\n- old\n{END}{after}"
    new = replace_block(note, DEFAULT_HEADING, [LINE])
    assert new.startswith(before + BEGIN) and new.endswith(END + after)
    assert new[len(before) : len(new) - len(after)] == f"{BEGIN}\n{LINE}\n{END}"


def test_no_todos_leaves_an_empty_block_so_old_lines_are_gone():
    first = replace_block(None, DEFAULT_HEADING, [LINE])
    empty = replace_block(first, DEFAULT_HEADING, [])
    assert LINE not in empty and f"{BEGIN}\n{END}" in empty


@pytest.mark.parametrize(
    "text",
    [
        f"{END}\n{BEGIN}\n",  # end before begin
        f"{BEGIN}\n{BEGIN}\n{END}\n",  # two begins
        f"{BEGIN}\n- a\n",  # no end
        f"- a\n{END}\n",  # no begin
        f"{BEGIN}\n{END}\n{BEGIN}\n{END}\n",  # two blocks
    ],
)
def test_damaged_markers_are_refused_not_guessed(text):
    with pytest.raises(JournalTodoError, match="damaged"):
        replace_block(text, DEFAULT_HEADING, [LINE])


@pytest.mark.parametrize(
    "bad",
    [
        ["no dash"],
        ["- two\nlines"],
        ["- has a break\r"],
        [f"- {END} break out"],
        [f"- {BEGIN} again"],
        ["- <!-- comment"],
        ["- end -->"],
        ["- " + "x" * 400],
        [5],
        "- not a list",
        ["- ok"] * 21,
    ],
)
def test_lines_must_be_plain_generated_todo_lines(bad):
    with pytest.raises(JournalTodoError):
        check_lines(bad)


@pytest.mark.parametrize("bad", ["pkpipe todo", "## ", "## <b>x</b>", "## a\n## b", "x" * 80, "#" * 7 + " h"])
def test_the_heading_is_one_plain_markdown_heading(bad):
    with pytest.raises(JournalTodoError):
        check_heading(bad)
    assert check_heading("### Owner todo ") == "### Owner todo"
