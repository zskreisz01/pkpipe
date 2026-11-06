"""Spec 015 FR-001: the improvement backlog tools; the owner's fields stay the owner's."""

from __future__ import annotations

import pytest

from pkpipe.domain.improvement import item_id_for
from pkpipe.obsidian import improvements
from pkpipe.obsidian.errors import InvalidRows, PathRejected
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault

SRC = "2-Areas/Journal/2026-10-30.md"
STATEMENT = "Stretch for ten minutes every morning."
ID = item_id_for(SRC, STATEMENT)


def _new(**extra):
    return {
        "source_note": SRC,
        "date": "2026-10-30",
        "statement": STATEMENT,
        "area": "personal",
        "type": "habit",
        **extra,
    }


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    for f in ("0-INBOX", "2-Areas/Journal", "3-Resources"):
        (root / f).mkdir(parents=True)
    return Vault(VaultPolicy(vault_dir=root))


def _file(vault):
    return vault.policy.root / vault.policy.improvement_file


def _owner_edit(vault, old, new):
    p = _file(vault)
    p.write_text(p.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")


def test_new_rows_are_proposed_with_a_derived_id_and_roundtrip(vault):
    res = vault.update_improvements([_new()])
    assert res["inserted"] == [ID]
    [row] = vault.read_improvements()["rows"]
    assert row["id"] == ID and row["status"] == "proposed" and row["source_note"] == SRC
    assert row["statement"] == STATEMENT and row["area"] == "personal" and "verdict" not in row
    text = _file(vault).read_text(encoding="utf-8")
    assert f"[[{SRC.removesuffix('.md')}]]" in text and text.startswith("# Improvement backlog")


def test_an_agent_cannot_start_an_item_in_any_other_status(vault):
    """FR-001: only `proposed` is set by the agent."""
    for status in ("active", "reviewing", "kept", "adjusted", "dropped"):
        with pytest.raises(InvalidRows, match="proposed"):
            vault.update_improvements([_new(status=status)])
    assert not _file(vault).exists()


def test_an_agent_cannot_change_the_status_or_other_owner_fields(vault):
    vault.update_improvements([_new()])
    for field, value in (
        ("status", "active"),
        ("statement", "Something else."),
        ("area", "work"),
        ("type", "experiment"),
        ("success_criterion", "invented"),
        ("review_date", "2026-12-01"),
        ("date", "2026-10-01"),
    ):
        with pytest.raises(InvalidRows, match="owner"):
            vault.update_improvements([{"id": ID, field: value}])
    assert vault.read_improvements()["rows"][0]["status"] == "proposed"


def test_the_owners_status_is_never_overwritten_even_when_the_pipeline_repeats_a_stale_one(vault):
    vault.update_improvements([_new()])
    _owner_edit(vault, "| proposed |", "| active |")
    with pytest.raises(InvalidRows, match="owner"):
        vault.update_improvements([{"id": ID, "status": "proposed"}])
    assert vault.read_improvements()["rows"][0]["status"] == "active"


def test_review_fields_can_be_written_on_existing_rows_only(vault):
    vault.update_improvements([_new()])
    res = vault.update_improvements(
        [
            {
                "id": ID,
                "evidence": '2026-10-31 (+) "q"',
                "verdict": "keep",
                "reason": "ok",
                "next_experiment": "go on",
            }
        ]
    )
    assert res["updated"] == [ID]
    row = vault.read_improvements()["rows"][0]
    assert (
        row["verdict"] == "keep" and row["evidence"].startswith("2026-10-31") and row["status"] == "proposed"
    )
    with pytest.raises(InvalidRows, match="weekly review"):
        vault.update_improvements([_new(statement="A new one.", verdict="drop")])
    with pytest.raises(InvalidRows):
        vault.update_improvements([{"id": ID, "verdict": "delete"}])


def test_a_new_row_id_must_be_the_hash_of_source_and_statement(vault):
    with pytest.raises(InvalidRows, match="hash"):
        vault.update_improvements([_new(id="deadbeefdead")])
    vault.update_improvements([_new(id=ID)])  # the right one is fine
    assert vault.update_improvements([_new()])["unchanged"] == [ID]


def test_invalid_batch_writes_nothing_and_empty_batch_is_refused(vault):
    with pytest.raises(InvalidRows):
        vault.update_improvements([_new(), _new(statement="Second.", status="kept")])
    assert not _file(vault).exists()
    with pytest.raises(InvalidRows, match="no rows"):
        vault.update_improvements([])
    with pytest.raises(InvalidRows, match="required"):
        vault.update_improvements([{"statement": "x", "area": "work", "type": "habit"}])


def test_owner_edits_and_other_lines_survive_pipeline_writes(vault):
    vault.update_improvements([_new()])
    p = _file(vault)
    p.write_text(p.read_text(encoding="utf-8") + "\nMy own remark below the table.\n", encoding="utf-8")
    _owner_edit(vault, "| proposed |", "| active |")
    vault.update_improvements([{"id": ID, "verdict": "keep", "reason": "r"}])
    text = p.read_text(encoding="utf-8")
    assert "My own remark below the table." in text and "| active |" in text


def test_unreadable_rows_are_reported_not_fatal(vault):
    vault.update_improvements([_new()])
    _owner_edit(vault, "| personal |", "| hobby |")
    res = vault.read_improvements()
    assert res["rows"] == [] and len(res["invalid"]) == 1


def test_pipes_in_text_are_escaped_and_read_back(vault):
    vault.update_improvements([_new(statement="Use a | b notation.")])
    assert vault.read_improvements()["rows"][0]["statement"] == "Use a | b notation."


def test_the_backlog_is_a_managed_file_generic_tools_cannot_touch(vault):
    """The agent has no way around update_improvements' rules."""
    vault.update_improvements([_new()])
    with pytest.raises(PathRejected):
        vault.write_note(vault.policy.improvement_file, "x", None, True)
    with pytest.raises(PathRejected):
        vault.append_note(vault.policy.improvement_file, "x")
    with pytest.raises(PathRejected):
        vault.move_note(vault.policy.improvement_file, "2-Areas/elsewhere.md")


def test_backlog_path_stays_inside_the_tools_allowlist(tmp_path):
    root = tmp_path / "v"
    (root / "0-INBOX").mkdir(parents=True)
    # spec 020 FR-008: the layout is validated against the allowlists when the policy is built ...
    with pytest.raises(ValueError, match="improvement_file.*read_improvements"):
        VaultPolicy(vault_dir=root, improvement_file="0-INBOX/Improvement-Backlog.md")
    # ... and the resolver still refuses the path at run time (defence in depth)
    ok = VaultPolicy(vault_dir=root)
    moved = ok.layout.model_copy(update={"improvement_file": "0-INBOX/Improvement-Backlog.md"})
    bad = Vault(ok.model_copy(update={"layout": moved}))
    with pytest.raises(PathRejected, match="allowlist"):
        bad.read_improvements()


# -- read_notes ---------------------------------------------------------------------------------------------
def test_read_notes_returns_front_matter_bodies_and_lists_missing_notes(vault):
    """Spec 011: the context refresh reads the front matter (client, project) of project notes too."""
    root = vault.policy.root
    (root / "2-Areas/Journal/2026-10-30.md").write_text("---\nk: v\n---\nBody one.\n", encoding="utf-8")
    res = vault.read_notes(["2-Areas/Journal/2026-10-30.md", "2-Areas/Journal/2026-10-31.md"])
    assert res == {
        "notes": [
            {"path": "2-Areas/Journal/2026-10-30.md", "front_matter": {"k": "v"}, "body": "Body one.\n"}
        ],
        "missing": ["2-Areas/Journal/2026-10-31.md"],
    }
    assert vault.read_notes([]) == {"notes": [], "missing": []}


def test_read_notes_rejects_bad_paths_and_oversized_requests(vault):
    for bad in (["../x.md"], ["/etc/passwd"], ["4-Archive/../../x.md"], [".obsidian/x.md"], ["notes.txt"]):
        with pytest.raises(PathRejected):
            vault.read_notes(bad)
    with pytest.raises(PathRejected):
        vault.read_notes(["2-Areas/Journal/d.md"] * 61)
    with pytest.raises(PathRejected):
        vault.read_notes("2-Areas/Journal/d.md")  # type: ignore[arg-type]


def test_read_notes_does_not_follow_symlinks_out_of_the_vault(vault, tmp_path):
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    link = vault.policy.root / "2-Areas/Journal/link.md"
    link.symlink_to(outside)
    with pytest.raises(PathRejected):
        vault.read_notes(["2-Areas/Journal/link.md"])


def test_column_set_matches_the_documented_table():
    assert improvements.IMPROVEMENT_COLUMNS[:2] == ("status", "id")
