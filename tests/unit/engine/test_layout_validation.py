"""Spec 020 FR-008: the layout is validated when the config is loaded, validated or published."""

from __future__ import annotations

import json

import pytest

from pkpipe.domain.layout import VaultLayout, layout_structure_errors, policy_file_errors
from pkpipe.engine.registry import ConfigError, load_effective

REAL = {
    "inbox_dir": "1-Inbox",
    "triage_file": "1-Inbox/Inbox-Triage.md",
    "processing_list": "1-Inbox/Manual-Resource-Processing-List.md",
    "archive_dir": "6-Archive/Inbox",
    "inbox_archive_pattern": "6-Archive/Inbox/Inbox-Archive-{yyyy-MM-dd}.md",
    "quarantine_dir": "1-Inbox/_quarantine",
    "attachments_dir": "1-Inbox/attachments",
    "areas_dir": "3-Areas",
    "projects_dir": "2-Projects",
    "resources_dir": "4-Resources",
    "agent_tasks_dir": "3-Areas/Agent-Tasks",
    "tool_candidates_file": "3-Areas/System/System-Tool-Candidates.md",
    "improvement_file": "3-Areas/Self-Improvement/Improvement-Backlog.md",
    "proposals_dir": "3-Areas/Self-Improvement/System-Changes",
    "context_review_dir": "3-Areas/Context-Review",
    "journal_pattern": "DailyJournals/{yyyy}/{yyyy-MM-dd}.md",
}


def _load(pack_dir, tmp_path, layout=None, security=None):
    ov = tmp_path / "ov"
    ov.mkdir(exist_ok=True)
    if layout is not None:
        (ov / "layout.json").write_text(json.dumps(layout))
    if security is not None:
        (ov / "security.json").write_text(json.dumps(security))
    return load_effective(pack_dir, ov)


def test_the_real_layout_and_the_defaults_validate(pack_dir, tmp_path):
    assert layout_structure_errors(VaultLayout(**REAL)) == []
    assert _load(pack_dir, tmp_path, REAL).data["layout"]["inbox_dir"] == "1-Inbox"
    assert layout_structure_errors(VaultLayout()) == []


def test_a_half_moved_layout_fails_validation_and_names_path_and_tool(pack_dir, tmp_path):
    with pytest.raises(ConfigError) as exc:
        _load(pack_dir, tmp_path, {"inbox_dir": "1-Inbox", "triage_file": "X/T.md"})
    text = "; ".join(exc.value.errors)
    assert "layout.triage_file 'X/T.md'" in text and "tool 'update_triage'" in text
    assert "layout.processing_list '0-INBOX/Manual-Resource-Processing-List.md'" in text


def test_a_moved_inbox_with_everything_else_still_old_fails(pack_dir, tmp_path):
    with pytest.raises(ConfigError, match="quarantine_dir '0-INBOX/_quarantine'"):
        _load(pack_dir, tmp_path, {"inbox_dir": "1-Inbox"})


def test_security_quarantine_dir_is_optional_and_must_agree_with_the_layout(pack_dir, tmp_path):
    cfg = _load(pack_dir, tmp_path, REAL, security={"quarantine_dir": "1-Inbox/_quarantine"})
    assert cfg.data["security"]["quarantine_dir"] == "1-Inbox/_quarantine"
    with pytest.raises(ConfigError, match="differs from layout.json quarantine_dir"):
        _load(pack_dir, tmp_path, REAL, security={"quarantine_dir": "0-INBOX/_quarantine"})
    assert "quarantine_dir" not in json.loads((pack_dir / "security.json").read_text())


def test_a_category_folder_follows_resources_dir_and_unknown_placeholders_fail(pack_dir, tmp_path):
    cats = [{"id": "misc", "para_path": "{resouces_dir}/Misc"}]
    ov = tmp_path / "ov"
    ov.mkdir()
    (ov / "categories.json").write_text(json.dumps(cats))
    with pytest.raises(ConfigError, match="unknown placeholder"):
        load_effective(pack_dir, ov)
    assert VaultLayout(resources_dir="4-Resources").expand("{resources_dir}/Misc") == "4-Resources/Misc"
    assert VaultLayout().expand("plain/Folder") == "plain/Folder"


def test_overlay_layout_that_disagrees_with_the_vault_policy_fails(pack_dir, tmp_path):
    policy = tmp_path / "vault-policy.json"
    policy.write_text(json.dumps({"vault_dir": "/v", "layout": {**REAL, "inbox_dir": "1-Inbox"}}))
    ov = tmp_path / "ov"
    ov.mkdir()
    (ov / "layout.json").write_text(json.dumps({**REAL, "journal_pattern": "Elsewhere/{yyyy-MM-dd}.md"}))
    with pytest.raises(ConfigError) as exc:
        load_effective(pack_dir, ov, vault_policy=policy)
    text = "; ".join(exc.value.errors)
    assert "layout.journal_pattern 'Elsewhere/{yyyy-MM-dd}.md' differs from the vault policy layout" in text
    assert "outside the allowlist of tool 'read_note'" in text


def test_the_policy_file_check_names_the_tool_and_accepts_an_explicit_allow():
    layout = VaultLayout(**REAL)
    policy = {"layout": REAL}
    errors = policy_file_errors(layout, policy)
    assert any("journal_pattern" in e and "tool 'read_notes'" in e for e in errors)
    ok = {
        "layout": REAL,
        "allow": {"read_note": ["1-Inbox", "DailyJournals"], "read_notes": ["DailyJournals"]},
    }
    assert policy_file_errors(layout, ok) == []


def test_the_policy_check_reads_the_env_variable_and_the_legacy_keys(pack_dir, tmp_path, monkeypatch):
    policy = tmp_path / "p.json"
    policy.write_text(json.dumps({"vault_dir": "/v", "quarantine_dir": "0-INBOX/_q"}))
    monkeypatch.setenv("PKPIPE_VAULT_POLICY", str(policy))
    with pytest.raises(ConfigError, match="layout.quarantine_dir '0-INBOX/_quarantine' differs"):
        load_effective(pack_dir, None)
    monkeypatch.delenv("PKPIPE_VAULT_POLICY")
    assert load_effective(pack_dir, None).data["layout"]["quarantine_dir"] == "0-INBOX/_quarantine"
