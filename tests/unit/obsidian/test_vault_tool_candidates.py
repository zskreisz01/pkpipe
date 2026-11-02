"""Spec 016 FR-001 / SC-001: the System-Tool-Candidates list; the pipeline adds, the owner decides."""

from __future__ import annotations

import pytest

from pkpipe.domain import tool_candidate as tc
from pkpipe.obsidian import tool_candidates as store
from pkpipe.obsidian.errors import InvalidRows, PathRejected
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault

ROW = {
    "tool": "Metasea",
    "evaluation_note": "3-Resources/Tools/metasea-1.md",
    "component": "Web search",
    "benefit": "Faster queries.",
    "effort": "S",
}


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    for f in ("0-INBOX", "2-Areas", "3-Resources"):
        (root / f).mkdir(parents=True)
    return Vault(VaultPolicy(vault_dir=root))


def _file(vault):
    return vault.policy.root / vault.policy.tool_candidates_file


def _edit(vault, old, new):
    p = _file(vault)
    p.write_text(p.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")


def test_a_new_candidate_roundtrips_with_status_candidate_and_a_derived_id(vault):
    assert vault.update_tool_candidates([ROW])["inserted"] == ["metasea"]
    [row] = vault.read_tool_candidates()["rows"]
    assert row["id"] == "metasea" and row["status"] == "candidate" and row["effort"] == "S"
    assert row["evaluation_note"] == "3-Resources/Tools/metasea-1.md" and row["reason"] == ""
    text = _file(vault).read_text(encoding="utf-8")
    assert text.startswith("# System tool candidates") and "[[3-Resources/Tools/metasea-1]]" in text


def test_an_agent_cannot_start_a_candidate_in_another_status_or_with_owner_fields(vault):
    for status in ("investigating", "trial", "adopted", "rejected"):
        with pytest.raises(InvalidRows, match="candidate"):
            vault.update_tool_candidates([{**ROW, "status": status}])
    for field in ("reason", "adr", "ab", "evidence"):
        with pytest.raises(InvalidRows, match="owner"):
            vault.update_tool_candidates([{**ROW, field: "x"}])
    with pytest.raises(InvalidRows, match="id"):
        vault.update_tool_candidates([{**ROW, "id": "someone-else"}])
    with pytest.raises(InvalidRows, match="effort"):
        vault.update_tool_candidates([{**ROW, "effort": "XL"}])
    assert not _file(vault).exists()  # all or nothing: nothing was written


def test_a_batch_with_one_bad_row_writes_nothing(vault):
    with pytest.raises(InvalidRows, match="row 1"):
        vault.update_tool_candidates([ROW, {**ROW, "tool": "Other", "component": ""}])
    assert not _file(vault).exists()


def test_existing_rows_are_never_changed_by_the_pipeline(vault):
    """SC-001: a rejected (or adopted) tool stays as the owner left it, whatever the pipeline proposes."""
    vault.update_tool_candidates([ROW])
    _edit(vault, "| candidate |", "| rejected |")
    res = vault.update_tool_candidates([{**ROW, "benefit": "A different claim."}])
    assert res["unchanged"] == ["metasea"] and res["inserted"] == []
    [row] = vault.read_tool_candidates()["rows"]
    assert row["status"] == "rejected" and row["benefit"] == "Faster queries."


def test_the_owners_hand_edits_survive_new_rows(vault):
    vault.update_tool_candidates([ROW])
    _edit(vault, "# System tool candidates", "# System tool candidates\n\nMy own note above the table.")
    vault.update_tool_candidates([{**ROW, "tool": "Chronos", "component": "Scheduler"}])
    text = _file(vault).read_text(encoding="utf-8")
    assert "My own note above the table." in text and "Metasea" in text and "Chronos" in text


def test_hand_edited_rule_violations_are_reported_on_read(vault):
    vault.update_tool_candidates([ROW])
    _edit(vault, "| candidate |", "| adopted |")
    out = vault.read_tool_candidates()
    problems = {v["problem"] for v in out["violations"]}
    assert problems == {"adopted without an ADR", "adopted without an A/B result 'better'"}


def test_unreadable_rows_are_listed_not_fatal(vault):
    vault.update_tool_candidates([ROW])
    _edit(vault, "| S |", "| XL |")
    out = vault.read_tool_candidates()
    assert out["rows"] == [] and "unreadable" in out["invalid"][0]["text"]


def test_the_vault_policy_confines_the_list_to_areas(vault):
    assert vault.policy.tool_candidates_file in vault.policy.managed_files
    with pytest.raises(PathRejected):
        vault.write_note("2-Areas/System/System-Tool-Candidates.md", "overwritten", overwrite=True)


def test_the_owner_walks_a_candidate_to_adopted_with_adr_and_ab(vault):
    path = _file(vault)
    vault.update_tool_candidates([ROW])
    ab = tc.AbEvidence(tool="Metasea", verdict="better", gate_green=True, summary="better (2 metrics)")
    store.owner_set_status(path, "metasea", tc.Status.INVESTIGATING)
    store.owner_set_status(path, "Metasea", tc.Status.TRIAL, ab=ab)
    with pytest.raises(ValueError, match="ADR"):
        store.owner_set_status(path, "metasea", tc.Status.ADOPTED, adr="0002-x.md", adr_exists=False, ab=ab)
    store.owner_set_status(path, "metasea", tc.Status.ADOPTED, adr="0002-x.md", adr_exists=True, ab=ab)
    [row] = vault.read_tool_candidates()["rows"]
    assert row["status"] == "adopted" and row["adr"] == "0002-x.md" and row["ab"].startswith("better")
    assert vault.read_tool_candidates()["violations"] == []


def test_rejection_keeps_the_reason_and_reopening_needs_evidence(vault):
    path = _file(vault)
    vault.update_tool_candidates([ROW])
    with pytest.raises(ValueError, match="reason"):
        store.owner_set_status(path, "metasea", tc.Status.REJECTED)
    store.owner_set_status(path, "metasea", tc.Status.REJECTED, reason="Leaks memory under load")
    [row] = vault.read_tool_candidates()["rows"]
    assert row["status"] == "rejected" and row["reason"] == "Leaks memory under load"
    with pytest.raises(ValueError, match="new evidence"):
        store.owner_set_status(path, "metasea", tc.Status.CANDIDATE)
    store.owner_set_status(path, "metasea", tc.Status.CANDIDATE, evidence="v2 fixes the leak")
    [row] = vault.read_tool_candidates()["rows"]
    assert row["status"] == "candidate" and row["evidence"] == "v2 fixes the leak"


def test_an_unknown_candidate_is_refused(vault):
    with pytest.raises(ValueError, match="no candidate"):
        store.owner_set_status(_file(vault), "ghost", tc.Status.INVESTIGATING)
