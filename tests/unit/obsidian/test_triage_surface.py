"""Spec 023 T002-T003: ``triage.surface`` (vault | web), the data file and the lossless move between them."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from pkpipe.domain.layout import VaultLayout, layout_structure_errors, policy_file_errors
from pkpipe.domain.triage import item_id_for
from pkpipe.engine.config_models import TriageConfig, UiConfig, validate_effective
from pkpipe.obsidian import lists
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.surface import PAGE_MARKER, has_table, split_table
from pkpipe.obsidian.vault import Vault

URL = "https://mini.tail1234.ts.net"
NOW = datetime(2026, 11, 6, 20, 0)
PAGE = "0-INBOX/Inbox-Triage.md"
DATA = "0-INBOX/_pkpipe/triage-data.md"
HEAD10 = "| confirm | item | resource | suggested | override | state | kind | needs_owner | topic | comment |"
SEP10 = "|" + " --- |" * 10


def vault_for(root, surface, *, now=NOW):
    return Vault(
        VaultPolicy(vault_dir=root, triage_surface=surface, ui_public_url=URL), now=lambda: now
    )


def live_table(n=54):
    """A table shaped like the owner's live file: confirmed rows, a stray ``[ x]``, image embeds with the
    escaped pipe, comments with odd text."""
    rows = []
    for i in range(n):
        ident = f"{i:012x}"
        mark = "[ x]" if i == 3 else "[x]" if i % 5 else "[ ]"
        if i % 7 == 0:
            res, kind, topic = f"![[photo-{i}.jpg\\|200]]", "image", f"a topic {i}"
        else:
            res, kind, topic = f"https://example.org/p/{i}", "link", ""
        comment = "odd \\| text [[wiki]] ünï" if i % 11 == 0 else ""
        state = "processed" if i % 3 == 0 else "suggested"
        rows.append(
            f"| {mark} | {ident} | {res} | tool | {'misc' if i % 13 == 0 else ''} | {state} | {kind} | "
            f"{'yes' if kind == 'image' and i % 2 else ''} | {topic} | {comment} |"
        )
    return "\n".join([HEAD10, SEP10, *rows]) + "\n"


def write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def table_lines(text):
    return [ln for ln in text.splitlines() if ln.startswith("|")]


def test_the_data_file_defaults_below_the_inbox_folder_and_follows_it():
    assert VaultLayout().triage_data == "0-INBOX/_pkpipe/triage-data.md"
    assert VaultLayout(inbox_dir="1-Inbox", triage_file="1-Inbox/T.md").triage_data == (
        "1-Inbox/_pkpipe/triage-data.md"
    )


def test_the_data_file_is_validated_like_the_other_layout_paths():
    for bad in ("../x.md", "/abs/x.md", "0-INBOX/x.txt", ".hidden/x.md"):
        with pytest.raises(ValidationError):
            VaultLayout(triage_data_file=bad)
    with pytest.raises(ValidationError, match="same file"):
        VaultLayout(triage_data_file="0-INBOX/Inbox-Triage.md")
    with pytest.raises(ValidationError, match="quarantine"):
        VaultLayout(triage_data_file="0-INBOX/_quarantine/data.md")
    outside = VaultLayout(triage_data_file="4-Archive/data.md")
    assert any("triage_data_file" in e for e in layout_structure_errors(outside))
    assert layout_structure_errors(VaultLayout()) == []


def test_the_data_file_is_in_the_snapshot_scope_and_managed(tmp_path):
    layout = VaultLayout(triage_data_file="0-INBOX/sub/data.md")
    assert "0-INBOX" in layout.snapshot_roots()
    assert "data.md" in layout.managed_names
    policy = VaultPolicy(vault_dir=tmp_path, layout=layout)
    assert "0-INBOX/sub/data.md" in policy.managed_files


def test_the_policy_surface_must_agree_with_the_config():
    layout = VaultLayout()
    assert policy_file_errors(layout, {"triage_surface": "web"}, "web") == []
    errors = policy_file_errors(layout, {}, "web")
    assert any("triage_surface" in e and "'web'" in e for e in errors)
    assert policy_file_errors(layout, {}, "vault") == []


def test_config_models_for_surface_and_ui():
    assert TriageConfig().surface == "vault"
    with pytest.raises(ValidationError):
        TriageConfig(surface="both")
    ui = UiConfig(public_url="https://Mini.tail1234.ts.net/", allowed_hosts=["Mini.Tail1234.ts.net", "100.113.168.62"])
    assert ui.public_url == "https://Mini.tail1234.ts.net"
    assert ui.hosts() == ["mini.tail1234.ts.net", "100.113.168.62"]
    for bad in ("ftp://x", "https://u:p@x", "https://x/?q=1", "nothing"):
        with pytest.raises(ValidationError):
            UiConfig(public_url=bad)
    for bad in ("https://x", "x/y", "a b", "x:8787", ""):
        with pytest.raises(ValidationError):
            UiConfig(allowed_hosts=[bad])
    assert UiConfig(allowed_hosts=["::1"]).allowed_hosts == ["[::1]"]
    assert UiConfig(public_url="http://100.1.2.3:8787").hosts() == ["100.1.2.3"]


def test_web_surface_needs_a_public_url_in_the_effective_config():
    assert validate_effective({"triage": {"surface": "web"}, "ui": {"public_url": None}}) != []
    assert validate_effective({"triage": {"surface": "web"}, "ui": {"public_url": URL}}) == []
    assert validate_effective({"triage": {"surface": "vault"}}) == []


def test_vault_surface_is_unchanged(tmp_path):
    vault = vault_for(tmp_path, "vault")
    result = vault.update_triage([{"resource": "0-INBOX/a.md", "ai_suggested_category": "misc"}])
    assert result["migration"] is None
    assert has_table((tmp_path / PAGE).read_text(encoding="utf-8"))
    assert not (tmp_path / DATA).exists()
    assert vault.read_triage()["rows"][0]["item_id"] == item_id_for("0-INBOX/a.md")


def test_web_surface_new_table_goes_to_the_data_file_and_the_page_is_a_summary(tmp_path):
    vault = vault_for(tmp_path, "web")
    vault.update_triage([{"resource": "0-INBOX/a.md", "ai_suggested_category": "misc"}])
    data = (tmp_path / DATA).read_text(encoding="utf-8")
    page = (tmp_path / PAGE).read_text(encoding="utf-8")
    assert has_table(data) and len(table_lines(data)) == 3
    assert table_lines(page) == [] and "|" not in page.replace("·", "")
    assert page.startswith("---\npkpipe: triage-summary\n---\n")
    assert "# Inbox triage" in page and f"[{URL}/triage]({URL}/triage)" in page
    assert PAGE_MARKER in page and "Waiting for your decision: 1" in page
    assert "Last triage run: 2026-11-06 20:00" in page
    assert vault.read_triage()["rows"][0]["resource"] == "0-INBOX/a.md"


def test_page_to_data_file_moves_every_row_verbatim_and_is_idempotent(tmp_path):
    table = live_table()
    write(tmp_path, PAGE, "---\ntags: [triage]\n---\n# Inbox triage\n\nTick confirm.\n\n" + table)
    vault = vault_for(tmp_path, "web")
    plan = vault.triage_migration_plan()
    assert plan == {
        "direction": "to_web", "source": PAGE, "target": DATA, "rows": 54, "invalid": 0,
    }
    result = vault.update_triage([])
    assert result["migration"]["rows"] == 54
    data = (tmp_path / DATA).read_text(encoding="utf-8")
    page = (tmp_path / PAGE).read_text(encoding="utf-8")
    assert table_lines(data) == table_lines(table)  # every row and cell, byte for byte
    assert "[ x]" in data and "![[photo-0.jpg\\|200]]" in data and "odd \\| text" in data
    assert table_lines(page) == []
    assert page.startswith("---\ntags: [triage]\n---\n")  # the owner's front matter stays
    assert "Waiting for your decision:" in page
    # idempotent: nothing moves again, nothing changes
    assert vault.triage_migration_plan() is None
    again = vault.update_triage([])
    assert again["migration"] is None
    assert (tmp_path / DATA).read_text(encoding="utf-8") == data
    assert (tmp_path / PAGE).read_text(encoding="utf-8") == page
    assert len(vault.read_triage()["rows"]) == 54


def test_data_file_back_to_the_page_and_a_tombstone(tmp_path):
    table = live_table()
    write(tmp_path, PAGE, "# Inbox triage\n\n" + table)
    web = vault_for(tmp_path, "web")
    web.update_triage([])
    assert table_lines((tmp_path / PAGE).read_text(encoding="utf-8")) == []
    back = vault_for(tmp_path, "vault")
    plan = back.triage_migration_plan()
    assert plan["direction"] == "to_vault" and plan["rows"] == 54
    back.update_triage([])
    page = (tmp_path / PAGE).read_text(encoding="utf-8")
    assert table_lines(page) == table_lines(table)
    tomb = (tmp_path / DATA).read_text(encoding="utf-8")
    assert "|" not in tomb and "[[Inbox-Triage]]" in tomb and "not in use" in tomb
    assert back.triage_migration_plan() is None
    back.update_triage([])
    assert (tmp_path / PAGE).read_text(encoding="utf-8") == page  # idempotent
    # and forth again: lossless both ways, the tombstone is replaced by the table
    web.update_triage([])
    assert table_lines((tmp_path / DATA).read_text(encoding="utf-8")) == table_lines(table)


@pytest.mark.parametrize(
    "header",
    [
        "| confirm | item | resource | suggested | override | state |",
        "| confirm | item | resource | suggested | override | state | kind | needs_owner | topic |",
        HEAD10,
        # an unknown 11th column: not a header the engine writes, but the move keeps every cell
        HEAD10[:-1] + "| extra |",
    ],
)
def test_real_header_variants_lose_no_cell(tmp_path, header):
    from pkpipe.obsidian.tables import split_cells

    width = header.count("|") - 1
    cells = ["[ x]", "000000000001", "![[a.jpg\\|200]]", "tool", "", "suggested", "image", "yes", "t", "c", "z"]
    plain = ["[x]", "000000000002", "u", "misc", "", "processed", "", "", "", "", ""]
    rows = [f"| {' | '.join(r[:width])} |" for r in (cells, plain)]
    text = "# Inbox triage\n\n" + "\n".join([header, "|" + " --- |" * width, *rows]) + "\n"
    write(tmp_path, PAGE, text)
    vault_for(tmp_path, "web").update_triage([])
    data = table_lines((tmp_path / DATA).read_text(encoding="utf-8"))
    original = table_lines(text)
    assert table_lines((tmp_path / PAGE).read_text(encoding="utf-8")) == []
    if width == 11:  # not a header the engine writes: the lines move untouched
        assert data == original
        return
    # a header the engine knows is widened to the full one (spec 022): every original cell is still there
    assert len(data) == len(original)
    for old, new in zip(original, data, strict=True):
        if old.startswith("| ---") or "---" in old.split("|")[1]:
            continue
        assert split_cells(new)[:width] == split_cells(old)


def test_unreadable_rows_move_too_and_are_counted_in_the_plan(tmp_path):
    text = live_table(4) + "| [x] | broken row with too few cells |\n"
    write(tmp_path, PAGE, "# Inbox triage\n\n" + text)
    vault = vault_for(tmp_path, "web")
    assert vault.triage_migration_plan()["invalid"] == 1
    vault.update_triage([])
    data = (tmp_path / DATA).read_text(encoding="utf-8")
    assert "| [x] | broken row with too few cells |" in data
    assert len(vault.read_triage()["invalid"]) == 1


def test_owner_prose_on_the_retired_page_is_kept_in_the_data_file(tmp_path):
    write(tmp_path, PAGE, "# Inbox triage\n\nMy own reminder: call the dentist.\n\n" + live_table(3))
    vault_for(tmp_path, "web").update_triage([])
    assert "call the dentist" in (tmp_path / DATA).read_text(encoding="utf-8")
    assert "call the dentist" not in (tmp_path / PAGE).read_text(encoding="utf-8")


def test_before_the_move_reads_and_owner_decisions_use_the_file_that_holds_the_table(tmp_path):
    """Switching the config never shows an empty table: until the triage run moves it, the table is read
    (and decided on) where it is."""
    write(tmp_path, PAGE, "# Inbox triage\n\n" + live_table(5))
    vault = vault_for(tmp_path, "web")
    assert len(vault.read_triage()["rows"]) == 5
    vault.owner_comment(f"{1:012x}", "decided in the UI")
    assert "decided in the UI" in (tmp_path / PAGE).read_text(encoding="utf-8")
    assert not (tmp_path / DATA).exists()  # no move without a triage write
    assert has_table((tmp_path / PAGE).read_text(encoding="utf-8"))  # and the page was not overwritten


def test_an_interrupted_move_merges_the_missing_rows_and_the_surface_file_wins(tmp_path):
    table = live_table(6)
    write(tmp_path, PAGE, "# Inbox triage\n\n" + table)
    vault_for(tmp_path, "web").update_triage([])
    data = (tmp_path / DATA).read_text(encoding="utf-8")
    # the owner added a row on the old page after the data file was written, and changed a row there
    extra = "| [x] | 00000000ffff | https://example.org/new | tool |  | suggested |  |  |  |  |"
    old_row = table_lines(table)[2]
    changed = old_row.replace("[x]", "[ ]", 1) if "[x]" in old_row else old_row.replace("[ ]", "[x]", 1)
    page = "# Inbox triage\n\n" + table.replace(old_row, changed) + extra + "\n"
    write(tmp_path, PAGE, page)
    vault_for(tmp_path, "web").update_triage([])
    merged = (tmp_path / DATA).read_text(encoding="utf-8")
    assert extra in merged  # the missing row arrived
    assert old_row in merged and changed not in merged  # the surface file's own row wins
    assert all(ln in merged for ln in table_lines(data))
    assert table_lines((tmp_path / PAGE).read_text(encoding="utf-8")) == []


def test_the_page_summary_counts_and_keeps_the_run_time_on_a_ui_decision(tmp_path):
    vault = vault_for(tmp_path, "web")
    vault.update_triage(
        [
            {"resource": "0-INBOX/a.md", "ai_suggested_category": "tool"},
            {"resource": "0-INBOX/b.md", "ai_suggested_category": "tool"},
            {"resource": "0-INBOX/img.jpg", "ai_suggested_category": "none", "kind": "image", "needs_owner": True},
        ]
    )
    page = (tmp_path / PAGE).read_text(encoding="utf-8")
    assert "Waiting for your decision: 3" in page and "Images that need a category from you: 1" in page
    later = Vault(
        VaultPolicy(vault_dir=tmp_path, triage_surface="web", ui_public_url=URL),
        now=lambda: datetime(2026, 11, 7, 9, 30),
    )
    later.owner_decide(item_id_for("0-INBOX/a.md"), confirm=True)
    page = (tmp_path / PAGE).read_text(encoding="utf-8")
    assert "Waiting for your decision: 2" in page
    assert "Last triage run: 2026-11-06 20:00" in page  # a UI decision is not a triage run
    later.update_triage([])
    assert "Last triage run: 2026-11-07 09:30" in (tmp_path / PAGE).read_text(encoding="utf-8")


def test_the_page_has_nothing_but_front_matter_title_link_and_summary(tmp_path):
    vault = vault_for(tmp_path, "web")
    vault.update_triage([{"resource": "0-INBOX/a.md", "ai_suggested_category": "tool"}])
    body = (tmp_path / PAGE).read_text(encoding="utf-8").split("---\n", 2)[2]
    lines = [ln for ln in body.splitlines() if ln.strip()]
    assert lines[0] == "# Inbox triage" and lines[1].startswith("Decide in the web UI: [")
    assert not any(ln.startswith("|") for ln in lines)
    assert {ln for ln in lines if ln.startswith("- ")} == {
        "- Waiting for your decision: 1",
        "- Images that need a category from you: 0",
        "- New-category proposals awaiting a category: 0",
        "- All rows: 1 (suggested 1)",
    }


def test_without_a_public_url_the_page_says_where_to_set_it(tmp_path):
    vault = Vault(VaultPolicy(vault_dir=tmp_path, triage_surface="web"), now=lambda: NOW)
    vault.update_triage([{"resource": "0-INBOX/a.md", "ai_suggested_category": "tool"}])
    assert "ui.public_url" in (tmp_path / PAGE).read_text(encoding="utf-8")


def test_the_data_file_is_a_managed_file_the_note_tools_never_write(tmp_path):
    from pkpipe.obsidian.errors import PathRejected

    vault = vault_for(tmp_path, "web")
    with pytest.raises(PathRejected, match="maintained"):
        vault.write_note(DATA, "x", overwrite=True)
    with pytest.raises(PathRejected):
        vault.write_note(PAGE, "x", overwrite=True)


def test_split_table_finds_the_block_with_front_matter_and_prose_around_it():
    text = "---\na: 1\n---\n# T\n\nprose\n\n" + live_table(2) + "\nafter\n"
    block = split_table(text)
    assert block is not None and block.before.endswith("prose\n\n") and block.after == "\nafter\n"
    assert split_table("no table here\n| a | b |\n| --- | --- |\n") is None
