"""Spec 020 (T003, T004): the triage rows carry ``kind`` and ``needs_owner`` without disturbing old files."""

from __future__ import annotations

from pkpipe.domain.triage import item_id_for
from pkpipe.obsidian import lists

OLD = (
    "# T\n\n"
    "| confirm | item | resource | suggested | override | state |\n"
    "| --- | --- | --- | --- | --- | --- |\n"
    "|[x]| a1 |  https://example.invalid/a | learning |  | suggested |\n"
    "| [ ] | b2 | plain text idea | misc | marketing | pending |\n"
)


def _row(resource, **extra):
    return {"resource": resource, "ai_suggested_category": "misc", **extra}


def test_note_rows_keep_the_six_column_table_byte_for_byte(tmp_path):
    p = tmp_path / "T.md"
    p.write_text(OLD)
    res = lists.update_triage(p, [{"item_id": "b2", "state": "released"}])
    assert res["updated"] == ["b2"]
    assert p.read_text() == OLD.replace("| pending |", "| released |")
    assert lists.read_triage(p)["rows"][0]["kind"] == "note"


def test_a_new_file_with_note_rows_only_has_six_columns(tmp_path):
    p = tmp_path / "T.md"
    lists.update_triage(p, [_row("0-INBOX/x.md")])
    header = next(ln for ln in p.read_text().splitlines() if ln.startswith("| confirm"))
    assert header == "| confirm | item | resource | suggested | override | state |"


def test_first_list_row_widens_header_and_rows_once(tmp_path):
    p = tmp_path / "T.md"
    p.write_text(OLD)
    img = _row("IMG_1.jpg", ai_suggested_category="none", kind="image", needs_owner=True)
    lists.update_triage(p, [img])
    lines = p.read_text().splitlines()
    assert "| confirm | item | resource | suggested | override | state | kind | needs_owner |" in lines
    assert "| --- | --- | --- | --- | --- | --- | --- | --- |" in lines
    rows = {r["item_id"]: r for r in lists.read_triage(p)["rows"]}
    assert rows["a1"]["confirm"] is True and rows["a1"]["kind"] == "note"  # the owner's tick survived
    assert rows[item_id_for("IMG_1.jpg")]["kind"] == "image" and rows[item_id_for("IMG_1.jpg")]["needs_owner"]
    # a later write changes only the changed row
    before = p.read_text()
    lists.update_triage(p, [{"item_id": "b2", "state": "released"}])
    after = p.read_text()
    assert [o for o, n in zip(before.splitlines(), after.splitlines(), strict=True) if o != n] != []
    assert len(before.splitlines()) == len(after.splitlines())


def test_unknown_kind_is_refused(tmp_path):
    import pytest

    from pkpipe.obsidian.errors import InvalidRows

    with pytest.raises(InvalidRows, match="unknown kind"):
        lists.update_triage(tmp_path / "T.md", [_row("x", kind="video")])
