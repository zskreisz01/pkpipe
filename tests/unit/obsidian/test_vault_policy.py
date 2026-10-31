"""Spec 004 FR-002, SC-002, T001: allowlist and safe path resolver."""

from __future__ import annotations

import json
import os

import pytest

from pkpipe.obsidian.errors import PathRejected
from pkpipe.obsidian.policy import VaultPolicy, is_conflict_name

TRAVERSAL = [
    "../outside.md",
    "0-INBOX/../../outside.md",
    "0-INBOX/../1-Projects/../../x.md",
    "/etc/passwd.md",
    "~/secret.md",
    "..",
    "0-INBOX/./a.md",
    "0-INBOX//a.md",
    "0-INBOX\\..\\a.md",
    "C:/Windows/a.md",
    "0-INBOX/a.md\x00.txt",
    "",
    "   ",
    "0-INBOX/.hidden/a.md",
    ".obsidian/app.md",
    "0-INBOX/%2e%2e/a.md".replace("%2e%2e", ".."),
]


@pytest.fixture
def policy(synthetic_vault) -> VaultPolicy:
    return VaultPolicy.load(None, synthetic_vault)


@pytest.mark.parametrize("bad", TRAVERSAL)
@pytest.mark.parametrize("tool", ["read_note", "write_note", "append_note", "move_note"])
def test_traversal_and_absolute_paths_rejected(policy, bad, tool):
    """SC-002 / FR-002: '..', absolute, hidden and malformed paths never resolve."""
    with pytest.raises(PathRejected):
        policy.resolve(bad, tool)


def test_allowlist_is_per_tool(synthetic_vault):
    """FR-002: write_note may be narrower than read_note."""
    policy = VaultPolicy.load(None, synthetic_vault)
    policy.allow["write_note"] = ["3-Resources/Tools"]
    assert policy.resolve("0-INBOX/example-tool.md", "read_note").is_file()
    assert policy.resolve("3-Resources/Tools/x.md", "write_note").name == "x.md"
    for denied in ("0-INBOX/x.md", "3-Resources/Learning/x.md", "3-Resources/Tools.md"):
        with pytest.raises(PathRejected):
            policy.resolve(denied, "write_note")


def test_prefix_is_by_folder_not_by_string(synthetic_vault):
    """'0-INBOX-evil/x.md' must not match the allowed '0-INBOX' prefix."""
    policy = VaultPolicy.load(None, synthetic_vault)
    with pytest.raises(PathRejected):
        policy.resolve("0-INBOX-evil/x.md", "read_note")


def test_only_markdown_notes(policy):
    with pytest.raises(PathRejected):
        policy.resolve("0-INBOX/a.txt", "read_note")


def test_symlink_escape_rejected(synthetic_vault, tmp_path):
    """A symlink inside an allowed folder that points outside the vault is rejected after resolution."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    os.symlink(outside, synthetic_vault / "0-INBOX" / "link")
    policy = VaultPolicy.load(None, synthetic_vault)
    with pytest.raises(PathRejected):
        policy.resolve("0-INBOX/link/secret.md", "read_note")


def test_symlink_into_disallowed_vault_folder_rejected(synthetic_vault):
    """Symlink to another vault folder that this tool may not touch is rejected as well."""
    (synthetic_vault / "4-Archive").mkdir()
    os.symlink(synthetic_vault / "4-Archive", synthetic_vault / "0-INBOX" / "arch")
    policy = VaultPolicy.load(None, synthetic_vault)
    with pytest.raises(PathRejected):
        policy.resolve("0-INBOX/arch/a.md", "write_note")


def test_folder_kind_accepts_prefix_itself_but_file_does_not(policy):
    assert policy.resolve("0-INBOX", "list_folder", kind="folder").is_dir()
    assert policy.resolve("0-INBOX/", "list_folder", kind="folder").is_dir()
    with pytest.raises(PathRejected):
        policy.resolve("", "list_folder", kind="folder")
    with pytest.raises(PathRejected):
        policy.resolve("0-INBOX", "read_note")


def test_bad_allowlist_config_rejected(synthetic_vault):
    with pytest.raises(ValueError):
        VaultPolicy(vault_dir=synthetic_vault, allow={"read_note": ["../x"]})
    with pytest.raises(ValueError):
        VaultPolicy(vault_dir=synthetic_vault, allow={"delete_note": ["0-INBOX"]})


def test_load_requires_vault_dir():
    with pytest.raises(ValueError):
        VaultPolicy.load(None, None)


def test_load_config_overrides_one_tool_keeps_other_defaults(tmp_path, synthetic_vault):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"vault_dir": str(synthetic_vault), "allow": {"write_note": ["3-Resources"]}}))
    policy = VaultPolicy.load(cfg)
    assert policy.allow["write_note"] == ["3-Resources"]
    assert "0-INBOX" in policy.allow["read_note"]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("note (conflict).md", True),
        ("note (Conflict 2026-01-01).md", True),
        ("note.sync-conflict-20260101-abc.md", True),
        ("note.md", False),
        ("conflict-resolution-guide.md", False),
    ],
)
def test_conflict_names(name, expected):
    """T006: sync conflict copies are recognised, normal names are not."""
    assert is_conflict_name(name) is expected


def test_launchd_example_is_valid_and_loopback_only():
    """T007: the shipped plist example parses and binds loopback."""
    import plistlib

    from tests.conftest import REPO

    path = REPO / "deploy/launchd/io.pkpipe.obsidian-vault.plist.example"
    data = plistlib.loads(path.read_bytes())
    args = data["ProgramArguments"]
    assert args[args.index("--host") + 1] == "127.0.0.1" and "pkpipe.obsidian.server" in args
    assert data["KeepAlive"] is True
