"""Spec 021: an image row of the triage file shows the image in Obsidian: the resource cell is a local embed
``![[name|200]]`` (written with an escaped pipe inside the table), the item id stays that of the name."""

from __future__ import annotations

import pytest

from pkpipe.domain.triage import item_id_for
from pkpipe.obsidian import lists
from pkpipe.obsidian.errors import InvalidRows
from pkpipe.obsidian.models import TriageRow

NAME = "FB_IMG_0000000000001.jpg"
ID = item_id_for(NAME)


def image_row(**extra):
    return {
        "resource": NAME,
        "ai_suggested_category": "misc",
        "kind": "image",
        "topic": "A synthetic page.",
        **extra,
    }


def resource_cell(path):
    line = next(ln for ln in path.read_text(encoding="utf-8").splitlines() if ID in ln)
    return line


def test_an_image_row_renders_as_an_embed_with_the_width_and_an_escaped_pipe(tmp_path):
    p = tmp_path / "T.md"
    lists.update_triage(p, [image_row(embed_width=200)])
    line = resource_cell(p)
    assert f"![[{NAME}\\|200]]" in line  # the pipe of the alias is escaped so the table cell stays one cell
    assert line.count("|") - line.count("\\|") == 10  # nine cells of the widened table, ten separators


def test_the_row_parses_back_to_the_plain_name_and_the_id_is_unchanged(tmp_path):
    p = tmp_path / "T.md"
    lists.update_triage(p, [image_row(embed_width=200)])
    [row] = lists.read_triage(p)["rows"]
    assert row["resource"] == NAME and row["item_id"] == ID and row["embed_width"] == 200
    assert not lists.read_triage(p)["invalid"]


def test_width_zero_or_none_writes_the_plain_name(tmp_path):
    for width in (0, None):
        p = tmp_path / f"T{width}.md"
        lists.update_triage(p, [image_row(embed_width=width)])
        assert f"| {NAME} |" in resource_cell(p) and "![[" not in resource_cell(p)


def test_only_image_rows_get_an_embed(tmp_path):
    p = tmp_path / "T.md"
    lists.update_triage(
        p, [{"resource": "0-INBOX/note.md", "ai_suggested_category": "misc", "embed_width": 200}]
    )
    assert "![[" not in p.read_text(encoding="utf-8")


def test_a_later_update_keeps_the_embed_and_the_owner_decision(tmp_path):
    p = tmp_path / "T.md"
    lists.update_triage(p, [image_row(embed_width=200)])
    lists.owner_decide(p, ID, confirm=True)
    lists.update_triage(p, [{"item_id": ID, "state": "released"}])
    line = resource_cell(p)
    assert f"![[{NAME}\\|200]]" in line and "[x]" in line and "released" in line
    assert len(lists.read_triage(p)["rows"]) == 1


def test_an_old_plain_row_is_migrated_on_the_next_update_without_losing_the_decision(tmp_path):
    p = tmp_path / "T.md"
    lists.update_triage(p, [image_row()])  # written before the embed existed: plain name
    lists.owner_decide(p, ID, override="misc")
    other = {
        "resource": "OTHER_IMG.jpg",
        "ai_suggested_category": "misc",
        "kind": "image",
        "embed_width": 200,
    }
    lists.update_triage(p, [other])  # a run with the embed setting touches the file
    rows = {r["item_id"]: r for r in lists.read_triage(p)["rows"]}
    assert len(rows) == 2
    old = rows[ID]
    assert old["override"] == "misc" and old["embed_width"] == 200 and old["resource"] == NAME
    assert f"![[{NAME}\\|200]]" in resource_cell(p)


def test_a_hand_edited_embed_with_another_width_is_kept(tmp_path):
    p = tmp_path / "T.md"
    lists.update_triage(p, [image_row(embed_width=200)])
    text = p.read_text(encoding="utf-8").replace("\\|200]]", "\\|480]]")
    p.write_text(text, encoding="utf-8")
    lists.update_triage(p, [{"item_id": ID, "state": "pending"}])
    assert f"![[{NAME}\\|480]]" in resource_cell(p)


def test_a_plain_embed_without_a_width_parses_too(tmp_path):
    p = tmp_path / "T.md"
    lists.update_triage(p, [image_row(embed_width=200)])
    p.write_text(p.read_text(encoding="utf-8").replace("\\|200]]", "]]"), encoding="utf-8")
    [row] = lists.read_triage(p)["rows"]
    assert row["resource"] == NAME


def test_a_path_in_the_embed_target_is_the_resource_as_written_in_the_list():
    row = TriageRow(
        item_id=item_id_for("attachments/pic one.png"),
        resource="attachments/pic one.png",
        ai_suggested_category="misc",
        kind="image",
        embed_width=150,
    )
    assert row.to_cells()["resource"] == "![[attachments/pic one.png|150]]"
    back = TriageRow.from_cells(
        {**row.to_cells(), "confirm": "[ ]", "item": row.item_id, "suggested": "misc"}
    )
    assert back.resource == "attachments/pic one.png" and back.embed_width == 150


def test_an_embed_given_as_the_resource_of_a_new_row_is_unwrapped(tmp_path):
    p = tmp_path / "T.md"
    lists.update_triage(p, [image_row(resource=f"![[{NAME}|200]]")])
    [row] = lists.read_triage(p)["rows"]
    assert row["resource"] == NAME and row["item_id"] == ID


def test_a_remote_url_never_becomes_an_embed():
    with pytest.raises(ValueError):
        TriageRow(item_id="a", resource="x", ai_suggested_category="m", kind="image", embed_width=-1)
    row = TriageRow(
        item_id="a",
        resource="https://evil.example/x.png",
        ai_suggested_category="m",
        kind="image",
        embed_width=200,
    )
    assert "![[" not in row.to_cells()["resource"]


def test_a_row_without_an_embed_in_an_old_file_stays_byte_identical(tmp_path):
    p = tmp_path / "T.md"
    lists.update_triage(p, [{"resource": "0-INBOX/a.md", "ai_suggested_category": "misc"}])
    before = p.read_text(encoding="utf-8")
    with pytest.raises(InvalidRows):
        lists.update_triage(p, [{"item_id": "nope", "state": "released"}])
    assert p.read_text(encoding="utf-8") == before
