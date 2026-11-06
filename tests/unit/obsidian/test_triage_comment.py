"""Spec 022 T001-T003: the owner-owned `comment` column of the triage table."""

from __future__ import annotations

import random

import pytest

from pkpipe.domain.triage import item_id_for
from pkpipe.obsidian import lists
from pkpipe.obsidian.errors import InvalidRows
from pkpipe.obsidian.models import TriageRow
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault

FULL = "| confirm | item | resource | suggested | override | state | kind | needs_owner | topic | comment |"
OLD9 = (
    "# Triage\n\n| confirm | item | resource | suggested | override | state | kind | needs_owner | topic |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
)
OLD6 = (
    "# Triage\n\n| confirm | item | resource | suggested | override | state |\n"
    "| --- | --- | --- | --- | --- | --- |\n"
)


def note_row(name="0-INBOX/a.md", **extra):
    return {"resource": name, "ai_suggested_category": "misc", **extra}


def lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def test_a_new_triage_file_has_the_comment_column_last(tmp_path):
    p = tmp_path / "T.md"
    lists.update_triage(p, [note_row()])
    assert FULL in lines(p)
    [row] = lists.read_triage(p)["rows"]
    assert row["comment"] == ""


def test_the_pipeline_cannot_write_a_comment(tmp_path):
    p = tmp_path / "T.md"
    with pytest.raises(InvalidRows, match="comment is the owner's"):
        lists.update_triage(p, [note_row(comment="from the pipeline")])
    lists.update_triage(p, [note_row()])
    item = item_id_for("0-INBOX/a.md")
    with pytest.raises(InvalidRows, match="comment is the owner's"):
        lists.update_triage(p, [{"item_id": item, "comment": "x"}])
    assert lists.read_triage(p)["rows"][0]["comment"] == ""


def make_with_comment(tmp_path, text, **row):
    p = tmp_path / "T.md"
    lists.update_triage(p, [note_row(**row)])
    item = item_id_for(row.get("resource", "0-INBOX/a.md"))
    s = p.read_text(encoding="utf-8")
    out = []
    for ln in s.splitlines():
        if f"| {item} |" in ln:
            assert ln.endswith("|  |")  # an empty last cell
            ln = ln[: -len("|  |")] + f"| {text} |"
        out.append(ln)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return p, item


@pytest.mark.parametrize(
    "text",
    [
        "buy the book",
        "long " * 200,
        "a \\| b \\| c",
        "see [[Some note]] and [[Other\\|alias]] and ![[pic.png\\|200]]",
        "https://example.org/x?y=1 <b>bold</b> `code`",
        "tab\tinside  and  double  blanks",
        "Árvíztűrő tükörfúrógép 😀",
    ],
)
def test_a_comment_round_trips_through_every_rewrite(tmp_path, text):
    p, item = make_with_comment(tmp_path, text)
    before = lists.read_triage(p)["rows"][0]["comment"]
    assert before.startswith(text.split()[0])
    lists.update_triage(p, [{"item_id": item, "state": "pending"}])
    lists.owner_decide(p, item, confirm=True)
    lists.update_triage(p, [note_row("0-INBOX/b.md")])  # a new row rewrites the table
    lists.update_triage(p, [])  # the maintenance call
    after = lists.read_triage(p)["rows"]
    mine = next(r for r in after if r["item_id"] == item)
    assert mine["comment"] == before and mine["state"] == "pending" and mine["confirm"] is True


def test_surrounding_blanks_of_a_cell_are_layout_not_content(tmp_path):
    p, item = make_with_comment(tmp_path, "   padded text   ")
    assert lists.read_triage(p)["rows"][0]["comment"] == "padded text"
    lists.update_triage(p, [{"item_id": item, "state": "pending"}])
    assert lists.read_triage(p)["rows"][0]["comment"] == "padded text"


def test_a_comment_survives_the_image_embed_migration(tmp_path):
    p = tmp_path / "T.md"
    img = {"resource": "pic.png", "ai_suggested_category": "misc", "kind": "image", "topic": "A page."}
    lists.update_triage(p, [img])
    item = item_id_for("pic.png")
    s = p.read_text(encoding="utf-8").replace("| A page. |  |", "| A page. | my own words \\| kept |")
    p.write_text(s, encoding="utf-8")
    lists.update_triage(
        p, [{"resource": "other.png", "ai_suggested_category": "misc", "kind": "image", "embed_width": 200}]
    )
    rows = {r["item_id"]: r for r in lists.read_triage(p)["rows"]}
    assert rows[item]["embed_width"] == 200 and rows[item]["comment"] == "my own words | kept"


def test_the_pipeline_may_overwrite_the_topic_of_an_image_row_but_never_the_comment(tmp_path):
    p = tmp_path / "T.md"
    lists.update_triage(
        p, [{"resource": "pic.png", "ai_suggested_category": "misc", "kind": "image", "topic": "old"}]
    )
    item = item_id_for("pic.png")
    p.write_text(p.read_text(encoding="utf-8").replace("| old |  |", "| old | mine |"), encoding="utf-8")
    lists.update_triage(p, [{"item_id": item, "topic": "new description"}])
    [row] = lists.read_triage(p)["rows"]
    assert row["topic"] == "new description" and row["comment"] == "mine"


def test_a_random_sequence_of_pipeline_writes_never_changes_the_comments(tmp_path):
    """SC-001: 100 random rewrites."""
    rng = random.Random(22)
    p = tmp_path / "T.md"
    names = [f"0-INBOX/n{i}.md" for i in range(6)]
    lists.update_triage(p, [note_row(n) for n in names])
    comments = {}
    s = p.read_text(encoding="utf-8")
    out = []
    for ln in s.splitlines():
        for i, n in enumerate(names):
            if f"| {item_id_for(n)} |" in ln and ln.endswith("|  |"):
                comments[item_id_for(n)] = f"c{i} \\| [[w{i}]] " + "x" * rng.randint(0, 50)
                ln = ln[: -len("|  |")] + f"| {comments[item_id_for(n)]} |"
        out.append(ln)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    want = {k: v.replace("\\|", "|") for k, v in comments.items()}
    for step in range(100):
        item = item_id_for(rng.choice(names))
        action = rng.choice(["state", "decide", "new", "maintenance", "topic"])
        try:
            if action == "state":
                lists.update_triage(p, [{"item_id": item, "state": rng.choice(["pending", "suggested"])}])
            elif action == "decide":
                lists.owner_decide(p, item, confirm=rng.random() < 0.5)
            elif action == "new":
                lists.update_triage(p, [note_row(f"0-INBOX/extra{step}.md")])
            elif action == "maintenance":
                lists.update_triage(p, [])
            else:
                lists.update_triage(p, [{"item_id": item, "topic": f"t{step}"}])
        except InvalidRows:
            pass  # a refused transition is fine: it must not have written anything
        got = {r["item_id"]: r["comment"] for r in lists.read_triage(p)["rows"]}
        assert all(got[k] == v for k, v in want.items()), step


# -- migration of older tables -------------------------------------------------------------------------------


def old_file(tmp_path, header, rows):
    p = tmp_path / "Old.md"
    p.write_text(header + "".join(rows), encoding="utf-8")
    return p


def test_a_nine_column_table_is_widened_on_the_next_write_and_loses_nothing(tmp_path):
    a, b = item_id_for("0-INBOX/a.md"), item_id_for("pic.png")
    p = old_file(
        tmp_path,
        OLD9,
        [
            f"| [x] | {a} | 0-INBOX/a.md | tool |  | suggested |  |  | my own text |\n",
            f"| [ ] | {b} | ![[pic.png\\|200]] | misc | misc | pending | image | yes | A page. |\n",
        ],
    )
    lists.update_triage(p, [note_row("0-INBOX/c.md")])
    assert FULL in lines(p)
    rows = {r["item_id"]: r for r in lists.read_triage(p)["rows"]}
    assert len(rows) == 3 and not lists.read_triage(p)["invalid"]
    assert rows[a]["confirm"] is True and rows[a]["ai_suggested_category"] == "tool"
    assert rows[b]["override"] == "misc" and rows[b]["embed_width"] == 200 and rows[b]["topic"] == "A page."


def test_owner_text_in_the_topic_of_a_non_image_row_moves_to_the_comment_once(tmp_path):
    note, link, text, img = (
        item_id_for(x) for x in ("0-INBOX/a.md", "https://e.example/l", "idea", "pic.png")
    )
    p = old_file(
        tmp_path,
        OLD9,
        [
            f"| [ ] | {note} | 0-INBOX/a.md | misc |  | suggested |  |  | owner words 1 |\n",
            f"| [ ] | {link} | https://e.example/l | misc |  | suggested | link |  | owner words 2 |\n",
            f"| [ ] | {text} | idea | misc |  | suggested | text |  | owner words 3 |\n",
            f"| [ ] | {img} | pic.png | misc |  | suggested | image |  | A description. |\n",
        ],
    )
    lists.update_triage(p, [])  # the maintenance call: the migration, nothing else
    rows = {r["item_id"]: r for r in lists.read_triage(p)["rows"]}
    assert (rows[note]["topic"], rows[note]["comment"]) == ("", "owner words 1")
    assert (rows[link]["topic"], rows[link]["comment"]) == ("", "owner words 2")
    assert (rows[text]["topic"], rows[text]["comment"]) == ("", "owner words 3")
    assert (rows[img]["topic"], rows[img]["comment"]) == ("A description.", "")  # pipeline-owned: stays
    lists.update_triage(p, [])
    lists.update_triage(p, [note_row("0-INBOX/z.md")])
    assert FULL in lines(p)
    again = {r["item_id"]: r for r in lists.read_triage(p)["rows"]}
    assert again[note]["comment"] == "owner words 1" and again[note]["topic"] == ""  # idempotent


def test_a_topic_typed_after_the_migration_is_left_alone_for_non_image_rows(tmp_path):
    a = item_id_for("0-INBOX/a.md")
    p = old_file(tmp_path, OLD9, [f"| [ ] | {a} | 0-INBOX/a.md | misc |  | suggested |  |  |  |\n"])
    lists.update_triage(p, [])
    p.write_text(
        p.read_text(encoding="utf-8").replace("|  |  |  |\n", "|  | typed later |  |\n"), encoding="utf-8"
    )
    lists.update_triage(p, [note_row("0-INBOX/b.md")])
    row = next(r for r in lists.read_triage(p)["rows"] if r["item_id"] == a)
    assert row["topic"] == "typed later" and row["comment"] == ""  # the rule runs only without the column


def test_a_six_column_table_is_widened_too_and_the_maintenance_call_is_a_no_op_when_current(tmp_path):
    a = item_id_for("0-INBOX/a.md")
    p = old_file(tmp_path, OLD6, [f"| [ ] | {a} | 0-INBOX/a.md | misc |  | suggested |\n"])
    lists.update_triage(p, [])
    assert FULL in lines(p) and lists.read_triage(p)["rows"][0]["item_id"] == a
    mtime = p.stat().st_mtime_ns
    text = p.read_text(encoding="utf-8")
    lists.update_triage(p, [])
    assert p.read_text(encoding="utf-8") == text and p.stat().st_mtime_ns == mtime  # nothing written


def test_the_maintenance_call_does_not_create_a_missing_file(tmp_path):
    p = tmp_path / "None.md"
    assert lists.update_triage(p, []) == {"inserted": [], "updated": [], "unchanged": [], "conflicts": []}
    assert not p.exists()


def test_an_owner_edit_also_migrates(tmp_path):
    a = item_id_for("0-INBOX/a.md")
    p = old_file(tmp_path, OLD9, [f"| [ ] | {a} | 0-INBOX/a.md | misc |  | suggested |  |  | owner |\n"])
    lists.owner_decide(p, a, confirm=True)
    row = lists.read_triage(p)["rows"][0]
    assert row["comment"] == "owner" and row["topic"] == "" and row["confirm"] is True


def test_unreadable_rows_stay_byte_for_byte(tmp_path):
    a = item_id_for("0-INBOX/a.md")
    bad = "| [ ] | broken row with | too | many | cells | a | b | c | d | e | f | g |\n"
    p = old_file(tmp_path, OLD9, [f"| [ ] | {a} | 0-INBOX/a.md | misc |  | suggested |  |  |  |\n", bad])
    lists.update_triage(p, [])
    assert bad in p.read_text(encoding="utf-8")
    assert lists.read_triage(p)["invalid"]


# -- the comment leaves the vault nowhere --------------------------------------------------------------------


def test_the_vault_tool_read_triage_leaves_the_comment_out_but_the_owner_channel_keeps_it(tmp_path):
    (tmp_path / "0-INBOX").mkdir()
    vault = Vault(VaultPolicy(vault_dir=tmp_path))
    vault.update_triage([note_row()])
    path = tmp_path / "0-INBOX" / "Inbox-Triage.md"
    item = item_id_for("0-INBOX/a.md")
    text = path.read_text(encoding="utf-8")
    path.write_text(
        "\n".join(
            ln[: -len("|  |")] + "| PRIVATE-OWNER-WORDS |" if f"| {item} |" in ln else ln
            for ln in text.splitlines()
        )
        + "\n",
        encoding="utf-8",
    )
    tool_result = vault.read_triage()
    assert "PRIVATE-OWNER-WORDS" not in repr(tool_result) and "comment" not in tool_result["rows"][0]
    assert (
        lists.read_triage(path)["rows"][0]["comment"] == "PRIVATE-OWNER-WORDS"
    )  # the owner channels read it


def test_the_row_model_round_trips_the_comment_and_the_schema_lists_it():
    row = TriageRow(item_id="a", resource="r", ai_suggested_category="m", comment="hello | there")
    assert row.to_cells()["comment"] == "hello | there"
    back = TriageRow.from_cells({**row.to_cells(), "item": "a", "suggested": "m"})
    assert back.comment == "hello | there"
    assert "comment" in TriageRow.model_json_schema()["properties"]
