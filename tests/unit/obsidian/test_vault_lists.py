"""Spec 004 US2, SC-001, FR-001, T004-T006: triage file and processing list as structured rows."""

from __future__ import annotations

import pytest

from pkpipe.domain.triage import ItemState, TriageItem, release
from pkpipe.obsidian import lists
from pkpipe.obsidian.errors import InvalidRows
from pkpipe.obsidian.models import ProcessingListRow, ProcessingStatus, item_id_for
from pkpipe.obsidian.tables import TableFile
from tests.support.triage_fixtures import HEADER, SEP, row, triage_fixtures

FIXTURES = triage_fixtures()


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_roundtrip_is_lossless_on_fixture(name):
    """SC-001: parse then render reproduces each of the 20 fixture files byte for byte."""
    text = FIXTURES[name]
    table = TableFile(text, lists.TRIAGE_COLUMNS, key="item")
    assert table.render() == text


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_upserting_unchanged_rows_is_a_noop_on_fixture(name):
    """SC-001: re-writing the rows we just read changes nothing (lossless write path)."""
    text = FIXTURES[name]
    table = TableFile(text, lists.TRIAGE_COLUMNS, key="item")
    for cells in table.rows():
        assert table.upsert(cells) == "unchanged"
    assert table.render() == text


def test_parse_exposes_confirm_override_per_item(tmp_path):
    """US2.1: checkbox and override column values are returned per item."""
    p = tmp_path / "t.md"
    p.write_text(FIXTURES["01-basic"])
    rows = {r["item_id"]: r for r in lists.read_triage(p)["rows"]}
    assert rows["a1"]["confirm"] is False and rows["a1"]["override"] is None
    assert rows["b2"]["confirm"] is True and rows["b2"]["ai_suggested_category"] == "learning"
    assert rows["c3"]["override"] == "marketing" and rows["c3"]["state"] == "pending"


def test_unreadable_rows_are_reported_not_fatal(tmp_path):
    p = tmp_path / "t.md"
    p.write_text(FIXTURES["12-malformed-row"].replace("[ ] | m2", "[?] | m2"))
    res = lists.read_triage(p)
    assert [r["item_id"] for r in res["rows"]] == ["m1"]
    assert len(res["invalid"]) == 2


def test_escaped_pipe_survives_parse(tmp_path):
    p = tmp_path / "t.md"
    p.write_text(FIXTURES["06-escaped-pipes"])
    assert lists.read_triage(p)["rows"][0]["resource"] == "https://e.invalid/?a=1|b"


def test_update_preserves_manual_edits_in_other_rows(tmp_path):
    """US2.2: only the addressed row changes; every other line stays as the owner left it."""
    p = tmp_path / "t.md"
    p.write_text(FIXTURES["05-prose-after"])
    lists.update_triage(
        p, []
    )  # spec 022: the first write widens the table (one re-serialisation of every row)
    text = p.read_text().replace("| [ ] | a1 |", "|[x]|   a1 |")  # then the owner's odd spacing
    p.write_text(text)
    res = lists.update_triage(p, [{"item_id": "c3", "state": "released"}])
    assert res["updated"] == ["c3"]
    new = p.read_text()
    old_lines, new_lines = text.splitlines(), new.splitlines()
    changed = [(o, n) for o, n in zip(old_lines, new_lines, strict=True) if o != n]
    assert len(old_lines) == len(new_lines) and len(changed) == 1
    assert "c3" in changed[0][1] and "| released |" in changed[0][1]
    assert "|[x]|   a1 |" in new  # untouched hand-edited row


def test_update_keeps_owner_confirm_and_override_when_agent_omits_them(tmp_path):
    p = tmp_path / "t.md"
    p.write_text(FIXTURES["01-basic"])
    lists.update_triage(p, [{"item_id": "b2", "state": "released"}, {"item_id": "c3", "state": "released"}])
    rows = {r["item_id"]: r for r in lists.read_triage(p)["rows"]}
    assert rows["b2"]["confirm"] is True and rows["b2"]["state"] == "released"
    assert rows["c3"]["override"] == "marketing"


def test_insert_new_row_uses_resource_hash_and_appends_inside_table(tmp_path):
    p = tmp_path / "t.md"
    p.write_text(FIXTURES["11-other-table-after"])
    res = lists.update_triage(p, [{"resource": "https://e.invalid/new", "ai_suggested_category": "tool"}])
    iid = item_id_for("https://e.invalid/new")
    assert res["inserted"] == [iid]
    lines = p.read_text().splitlines()
    idx = next(i for i, ln in enumerate(lines) if iid in ln)
    assert lines[idx - 1].startswith("| [ ] | c3 |") and lines[idx + 1] == ""
    assert lines[-1] == "| 1 | 2 |"  # the other table is untouched


def test_creates_file_with_header_when_missing(tmp_path):
    p = tmp_path / "sub" / "t.md"
    lists.update_triage(p, [{"resource": "r", "ai_suggested_category": "misc"}])
    text = p.read_text()
    assert HEADER in text and SEP in text and text.startswith("# Inbox triage")
    assert lists.read_triage(p)["rows"][0]["state"] == "suggested"


def test_appends_table_to_file_without_one(tmp_path):
    p = tmp_path / "t.md"
    p.write_text(FIXTURES["15-no-table"])
    lists.update_triage(p, [{"resource": "r", "ai_suggested_category": "misc"}])
    assert p.read_text().startswith(FIXTURES["15-no-table"] + "\n" + HEADER)


def test_invalid_batch_writes_nothing(tmp_path):
    """All-or-nothing: one bad row (unknown state / missing fields) aborts the whole update."""
    p = tmp_path / "t.md"
    p.write_text(FIXTURES["01-basic"])
    before = p.read_text()
    for bad in (
        [{"item_id": "a1", "state": "teleported"}],
        [{"item_id": "a1", "state": "released"}, {"resource": "x"}],
        [{"item_id": "zz"}],
    ):
        with pytest.raises(InvalidRows):
            lists.update_triage(p, bad)
        assert p.read_text() == before
    # an empty list is the maintenance call (spec 022), not an error: it widens the table and nothing else
    assert lists.update_triage(p, [])["inserted"] == []


def test_crlf_file_stays_crlf_after_update(tmp_path):
    p = tmp_path / "t.md"
    p.write_bytes(FIXTURES["03-crlf"].encode())
    lists.update_triage(
        p, [{"item_id": "a1", "state": "pending"}, {"resource": "n", "ai_suggested_category": "x"}]
    )
    data = p.read_bytes()
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")


@pytest.mark.parametrize(
    "bad",
    [
        {"item_id": "a1", "confirm": True},  # ticking the owner's box
        {"item_id": "b2", "confirm": False},  # un-ticking it
        {"item_id": "a1", "override": "tool"},  # overriding for the owner
        {"item_id": "c3", "override": ""},  # clearing the owner's override
        {"resource": "https://e.invalid/n", "ai_suggested_category": "x", "confirm": True},
        {"resource": "https://e.invalid/n", "ai_suggested_category": "x", "override": "tool"},
    ],
)
def test_pipeline_cannot_set_owner_decisions(tmp_path, bad):
    """Constitution VI / US2: confirm and override are owner decisions; the tool refuses to change them."""
    p = tmp_path / "t.md"
    p.write_text(FIXTURES["01-basic"])
    before = p.read_text()
    with pytest.raises(InvalidRows, match="owner"):
        lists.update_triage(p, [bad])
    assert p.read_text() == before


def test_repeating_the_owner_values_is_allowed(tmp_path):
    p = tmp_path / "t.md"
    p.write_text(FIXTURES["01-basic"])
    res = lists.update_triage(p, [{"item_id": "b2", "confirm": True, "override": None, "state": "released"}])
    assert res["updated"] == ["b2"]


@pytest.mark.parametrize(
    ("item", "state", "reason"),
    [
        ("a1", "released", "owner confirms"),  # unconfirmed item cannot be released
        ("c3", "processed", "not allowed"),  # pending -> processed skips the release
    ],
)
def test_state_changes_follow_the_domain_rules(tmp_path, item, state, reason):
    """Constitution V: code decides transitions; an agent cannot release an unconfirmed item."""
    p = tmp_path / "t.md"
    p.write_text(FIXTURES["01-basic"])
    with pytest.raises(InvalidRows, match=reason):
        lists.update_triage(p, [{"item_id": item, "state": state}])


def test_terminal_and_quarantined_states_cannot_be_left(tmp_path):
    """Constitution VI: leaving quarantine is the owner's call; processed is final."""
    p = tmp_path / "t.md"
    p.write_text(FIXTURES["01-basic"])
    lists.update_triage(
        p, [{"item_id": "b2", "state": "released"}, {"item_id": "c3", "state": "quarantined"}]
    )
    lists.update_triage(p, [{"item_id": "b2", "state": "processed"}])
    for item, state in (("b2", "pending"), ("c3", "released"), ("c3", "pending")):
        with pytest.raises(InvalidRows, match="not allowed"):
            lists.update_triage(p, [{"item_id": item, "state": state}])


def test_new_rows_start_suggested_with_hash_id(tmp_path):
    """Constitution VI + edge case 'same resource twice -> one item': ids are the resource hash."""
    p = tmp_path / "t.md"
    for bad in (
        {"resource": "r", "ai_suggested_category": "x", "state": "released"},
        {"item_id": "custom", "resource": "r", "ai_suggested_category": "x"},
    ):
        with pytest.raises(InvalidRows):
            lists.update_triage(p, [bad])
    assert not p.exists()
    lists.update_triage(p, [{"resource": " r ", "ai_suggested_category": "x"}])
    res = lists.update_triage(p, [{"resource": "r", "ai_suggested_category": "x"}])
    assert res["unchanged"] == [item_id_for("r")]
    assert len(lists.read_triage(p)["rows"]) == 1


def test_resource_of_existing_row_cannot_change(tmp_path):
    p = tmp_path / "t.md"
    p.write_text(FIXTURES["01-basic"])
    with pytest.raises(InvalidRows, match="resource"):
        lists.update_triage(p, [{"item_id": "a1", "resource": "https://e.invalid/other"}])


def test_conflict_copies_of_triage_file_are_reported(tmp_path):
    """T006 / edge case: sync conflict copies next to the triage file are reported, not merged."""
    p = tmp_path / "Inbox-Triage.md"
    p.write_text(FIXTURES["01-basic"])
    (tmp_path / "Inbox-Triage (conflict 2).md").write_text(FIXTURES["01-basic"])
    (tmp_path / "Other (conflict).md").write_text("x")
    assert lists.read_triage(p)["conflicts"] == ["Inbox-Triage (conflict 2).md"]
    assert lists.update_triage(p, [{"item_id": "a1", "state": "pending"}])["conflicts"] == [
        "Inbox-Triage (conflict 2).md"
    ]


def test_released_rows_feed_the_domain_release_rule(tmp_path):
    """Integration with the triage domain: rows read from the file drive the 06:00 release split."""
    p = tmp_path / "t.md"
    p.write_text(FIXTURES["01-basic"])
    rows = lists.read_triage(p)["rows"]
    items = [
        TriageItem(
            r["item_id"],
            r["resource"],
            r["ai_suggested_category"],
            r["confirm"],
            r["override"],
            ItemState(r["state"]),
        )
        for r in rows
    ]
    released, pending = release(items)
    assert {i.id for i in released} == {"b2", "c3"} and {i.id for i in pending} == {"a1"}
    lists.update_triage(p, [{"item_id": i.id, "state": i.state.value} for i in released + pending])
    states = {r["item_id"]: r["state"] for r in lists.read_triage(p)["rows"]}
    assert states == {"a1": "pending", "b2": "released", "c3": "released"}


# -- processing list --------------------------------------------------------------------


def _plist(tmp_path):
    p = tmp_path / "Manual-Resource-Processing-List.md"
    p.write_text(
        "# Manual list\n\n| item | category | note | nb_id | link | status |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| i1 | learning | [[Note One\\|alias]] | nb-1 | https://e.invalid/nb | ready |\n"
        "| i2 | tool | [[Note Two]] |  |  | manual |\n\nowner remark\n"
    )
    return p


def test_processing_list_upsert_updates_and_inserts_preserving_others(tmp_path):
    """US2.2 / T005: upsert by item id; hand edited rows and prose stay."""
    p = _plist(tmp_path)
    old = p.read_text()
    res = lists.upsert_processing_list(
        p,
        [
            {
                "item_id": "i1",
                "category": "learning",
                "obsidian_link": "[[Note One|alias]]",
                "nb_id": "nb-1",
                "link": "https://e.invalid/nb",
                "status": "done",
            },
            {"item_id": "i3", "category": "misc", "obsidian_link": "[[Three]]"},
        ],
    )
    assert res["updated"] == ["i1"] and res["inserted"] == ["i3"]
    new = p.read_text()
    assert "| i2 | tool | [[Note Two]] |  |  | manual |" in new and new.endswith("owner remark\n")
    assert new.count("\n") == old.count("\n") + 1
    rows = {r.item_id: r for r in lists.read_processing_list(p)}
    assert rows["i1"].status is ProcessingStatus.DONE and rows["i1"].obsidian_link == "[[Note One|alias]]"
    assert rows["i3"].status is ProcessingStatus.UNPROCESSED and rows["i3"].nb_id is None


def test_processing_list_idempotent(tmp_path):
    p = _plist(tmp_path)
    row_ = {"item_id": "i2", "category": "tool", "obsidian_link": "[[Note Two]]", "status": "manual"}
    before = p.read_text()
    assert lists.upsert_processing_list(p, [row_])["unchanged"] == ["i2"]
    assert p.read_text() == before


def test_processing_list_validation(tmp_path):
    p = tmp_path / "pl.md"
    with pytest.raises(InvalidRows):
        lists.upsert_processing_list(
            p, [{"item_id": "x", "category": "c", "obsidian_link": "[[n]]", "status": "bogus"}]
        )
    with pytest.raises(InvalidRows):
        lists.upsert_processing_list(p, [{"item_id": "x"}])
    with pytest.raises(InvalidRows):
        lists.upsert_processing_list(p, [])
    assert not p.exists()


def test_processing_row_cell_roundtrip():
    r = ProcessingListRow(
        item_id="a", category="c", obsidian_link="[[x|y]]", nb_id="n", link="u", status="failed"
    )
    assert ProcessingListRow.from_cells(r.to_cells()) == r


def test_item_id_is_stable_and_whitespace_insensitive():
    assert item_id_for("https://e.invalid/x") == item_id_for("  https://e.invalid/x \n")
    assert item_id_for("a") != item_id_for("b") and len(item_id_for("a")) == 12


def test_helper_row_format():
    assert row("[ ]", "i", "r", "c") == "| [ ] | i | r | c |  | suggested |"
