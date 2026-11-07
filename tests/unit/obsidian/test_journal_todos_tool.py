"""Spec 024 FR-003 and the security rules: the journal is the most sensitive note of the vault.

``write_journal_todos`` replaces one marked block and nothing else; it cannot read the journal out, cannot
write elsewhere, is off unless the policy grants the folder, and its spans carry no text. ``unreviewed_notes``
returns a count and one path, never text."""

from __future__ import annotations

from datetime import date

import pytest

from pkpipe.domain.journal_todos import BEGIN, END
from pkpipe.domain.layout import VaultLayout
from pkpipe.obsidian.errors import PathRejected
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault

DAY = date(2026, 11, 7)
LINE = "- 2026-11-07 - Review triage decisions - 3 items - [[1-Inbox/Inbox-Triage]]"
JOURNAL = "DailyJournals/2026/2026-11-07.md"


def _vault(tmp_path, *, grant=("DailyJournals",), today=DAY, layout=None):
    root = tmp_path / "vault"
    for f in ("1-Inbox", "DailyJournals/2026", "3-Resources/Tools", "2-Areas"):
        (root / f).mkdir(parents=True, exist_ok=True)
    allow = VaultLayout().default_allow()
    # the journal folder lies outside the PARA structure: the owner grants it for reading, and for the todo
    # block only when the todo block is wanted
    allow["read_note"] = [*allow["read_note"], "DailyJournals"]
    allow["read_notes"] = [*allow["read_notes"], "DailyJournals"]
    if grant:
        allow["write_journal_todos"] = list(grant)
    layout = {"journal_pattern": "DailyJournals/{yyyy}/{yyyy-MM-dd}.md", **(layout or {})}
    return Vault(VaultPolicy(vault_dir=root, layout=layout, allow=allow), today=lambda: today), root


def test_the_tool_is_off_unless_the_policy_grants_the_journal_folder(tmp_path):
    vault, root = _vault(tmp_path, grant=())
    with pytest.raises(PathRejected, match="outside the allowlist"):
        vault.write_journal_todos("2026-11-07", [LINE])
    assert not (root / JOURNAL).exists()


def test_a_missing_journal_is_created_with_only_the_section(tmp_path):
    vault, root = _vault(tmp_path)
    res = vault.write_journal_todos("2026-11-07", [LINE])
    assert res == {"path": JOURNAL, "lines": 1, "created": True}
    assert (root / JOURNAL).read_text(encoding="utf-8") == (
        f"---\ntype: journal\n---\n\n## pkpipe todo\n{BEGIN}\n{LINE}\n{END}\n"
    )


def test_an_existing_journal_keeps_every_other_byte_and_the_block_is_replaced_on_a_second_write(tmp_path):
    vault, root = _vault(tmp_path)
    note = root / JOURNAL
    original = "---\ntype: journal\ntags: [journal]\n---\n# 2026-11-07\n\n## Personal\nsecret thoughts\n"
    note.write_text(original, encoding="utf-8")
    assert vault.write_journal_todos("2026-11-07", [LINE])["created"] is False
    once = note.read_text(encoding="utf-8")
    assert once.startswith(original)
    vault.write_journal_todos("2026-11-07", ["- 2026-11-07 - Read tool evaluations - 2 items"])
    twice = note.read_text(encoding="utf-8")
    assert twice.startswith(original) and LINE not in twice and twice.count(BEGIN) == 1
    assert twice.endswith(f"{BEGIN}\n- 2026-11-07 - Read tool evaluations - 2 items\n{END}\n")


def test_text_the_owner_wrote_after_the_block_is_untouched(tmp_path):
    vault, root = _vault(tmp_path)
    note = root / JOURNAL
    vault.write_journal_todos("2026-11-07", [LINE])
    note.write_text(
        note.read_text(encoding="utf-8") + "\n## Evening close\n- Done today: x\n", encoding="utf-8"
    )
    vault.write_journal_todos("2026-11-07", [])
    text = note.read_text(encoding="utf-8")
    assert text.endswith("\n## Evening close\n- Done today: x\n") and LINE not in text


def test_the_answer_never_carries_journal_content(tmp_path):
    vault, root = _vault(tmp_path)
    (root / JOURNAL).write_text("# day\nTOP SECRET JOURNAL TEXT\n", encoding="utf-8")
    res = vault.write_journal_todos("2026-11-07", [LINE])
    assert set(res) == {"path", "lines", "created"} and "SECRET" not in repr(res)


def test_it_cannot_be_used_to_write_anywhere_but_the_journal_pattern(tmp_path):
    vault, root = _vault(tmp_path)
    # the path comes from the layout and the day, never from an argument: odd days are refused
    for bad in ("../2026-11-07", "2026-11-07.md", "2026/11/07", "today", "2026-13-40", "", None, 20261107):
        with pytest.raises(PathRejected):
            vault.write_journal_todos(bad, [LINE])  # type: ignore[arg-type]
    assert sorted(p.name for p in root.rglob("*.md")) == []


def test_only_today_and_yesterday_can_be_written(tmp_path):
    vault, root = _vault(tmp_path)
    vault.write_journal_todos("2026-11-06", [LINE])
    vault.write_journal_todos(
        "2026-11-08", [LINE]
    )  # a run just after midnight of another clock: one day slack
    for far in ("2026-11-04", "2026-11-10", "2020-01-01"):
        with pytest.raises(PathRejected, match="today or yesterday"):
            vault.write_journal_todos(far, [LINE])


def test_a_grant_that_does_not_cover_the_journal_pattern_is_a_policy_error(tmp_path):
    """The policy file and the layout must agree (spec 020 FR-008 style): a grant for another folder makes
    the policy invalid, so a mistyped folder is found at start, not at the first report."""
    with pytest.raises(ValueError, match="write_journal_todos"):
        _vault(tmp_path, grant=("Elsewhere",))


@pytest.mark.parametrize(
    "bad",
    [["- <!-- pkpipe-todo:end --> escape"], ["- a\n## injected heading"], ["no dash"], ["- x" * 200]],
)
def test_lines_cannot_break_out_of_the_block(tmp_path, bad):
    vault, root = _vault(tmp_path)
    with pytest.raises(PathRejected):
        vault.write_journal_todos("2026-11-07", bad)
    assert not (root / JOURNAL).exists()


def test_damaged_markers_stop_the_write_and_leave_the_journal_as_it_is(tmp_path):
    vault, root = _vault(tmp_path)
    broken = f"# d\n{END}\nsome text\n{BEGIN}\n"
    (root / JOURNAL).write_text(broken, encoding="utf-8")
    with pytest.raises(PathRejected, match="damaged"):
        vault.write_journal_todos("2026-11-07", [LINE])
    assert (root / JOURNAL).read_text(encoding="utf-8") == broken


def test_a_sync_conflict_copy_and_the_quarantine_are_never_written(tmp_path):
    vault, root = _vault(
        tmp_path, layout={"journal_pattern": "DailyJournals/{yyyy}/{yyyy-MM-dd} (conflict).md"}
    )
    with pytest.raises(PathRejected, match="conflict"):
        vault.write_journal_todos("2026-11-07", [LINE])


def test_the_heading_is_a_plain_heading_line(tmp_path):
    vault, root = _vault(tmp_path)
    for bad in ("not a heading", "## <script>", "## a\n## b"):
        with pytest.raises(PathRejected):
            vault.write_journal_todos("2026-11-07", [LINE], bad)
    vault.write_journal_todos("2026-11-07", [LINE], "## Owner todo")
    assert "## Owner todo\n" in (root / JOURNAL).read_text(encoding="utf-8")


def test_the_span_of_the_tool_has_no_text(tmp_path, monkeypatch):
    from pkpipe.obsidian import vault as vault_module

    seen: dict[str, object] = {}

    class Span:
        def set_attribute(self, key, value):
            seen[key] = value

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(vault_module, "tool_span", lambda name: Span())
    vault, _root = _vault(tmp_path)
    vault.write_journal_todos("2026-11-07", [LINE])
    assert set(seen) <= {"tool.server", "pkpipe.vault.rows"} and "triage" not in repr(seen)


# -- unreviewed_notes -------------------------------------------------------------------------------------
def _tool_note(root, name, *, reviewed=None, category="tool", item="abc", mtime=None):
    fm = ["---", f"category: {category}"]
    if item:
        fm.append(f"pkpipe_item: {item}")
    if reviewed is not None:
        fm.append(f"reviewed: {reviewed}")
    fm.append("---\nThe evaluation text that must never come back.\n")
    path = root / "3-Resources/Tools" / name
    path.write_text("\n".join(fm), encoding="utf-8")
    if mtime:
        import os

        os.utime(path, (mtime, mtime))
    return path


def test_unreviewed_notes_counts_pipeline_notes_without_reviewed_true_and_names_the_newest(tmp_path):
    vault, root = _vault(tmp_path)
    _tool_note(root, "old.md", mtime=1_700_000_000)
    _tool_note(root, "new.md", mtime=1_800_000_000)
    _tool_note(root, "read.md", reviewed="true")
    _tool_note(root, "read2.md", reviewed="Yes")
    _tool_note(root, "other.md", category="marketing")
    _tool_note(root, "mine.md", item="")  # not written by the pipeline
    _tool_note(root, "x (conflict).md")
    res = vault.unreviewed_notes("3-Resources/Tools", "tool")
    assert res == {"count": 2, "newest": "3-Resources/Tools/new.md"}
    assert "evaluation text" not in repr(res)


def test_reviewed_false_still_counts_and_a_missing_folder_is_zero(tmp_path):
    vault, root = _vault(tmp_path)
    _tool_note(root, "a.md", reviewed="false")
    assert vault.unreviewed_notes("3-Resources/Tools", "tool")["count"] == 1
    assert vault.unreviewed_notes("3-Resources/Nothing", "tool") == {"count": 0, "newest": None}


def test_unreviewed_notes_is_confined_to_its_allowlist_and_a_category_id(tmp_path):
    vault, root = _vault(tmp_path)
    for folder in ("DailyJournals", "1-Inbox", "../outside", ".obsidian"):
        with pytest.raises(PathRejected):
            vault.unreviewed_notes(folder, "tool")
    for bad in ("Tool", "a b", "../x", "", "x" * 50):
        with pytest.raises(PathRejected):
            vault.unreviewed_notes("3-Resources/Tools", bad)
