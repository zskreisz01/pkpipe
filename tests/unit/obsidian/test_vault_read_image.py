"""Spec 021 US3 (T001, T002): the vault tool ``read_image`` and the read-only ``attachments_dir``."""

from __future__ import annotations

import base64
import os

import pytest

from pkpipe.domain.layout import DEFAULT_LAYOUT, VaultLayout, layout_structure_errors
from pkpipe.obsidian.errors import ImageRejected, PathRejected
from pkpipe.obsidian.policy import TOOLS, VaultPolicy
from pkpipe.obsidian.scope import derive_scope
from pkpipe.obsidian.vault import Vault

# Signatures only: the tool checks the type, it never decodes (decoding is image.prepare, with Pillow).
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic" * 4
JPEG = b"\xff\xd8\xff\xe0" + b"synthetic" * 4
GIF = b"GIF89a" + b"synthetic" * 4
WEBP = b"RIFF" + b"\x10\x00\x00\x00" + b"WEBP" + b"synthetic"
ATT = "0-INBOX/attachments"


@pytest.fixture
def vault(tmp_path):
    (tmp_path / ATT).mkdir(parents=True)
    return Vault(VaultPolicy(vault_dir=tmp_path))


def put(vault: Vault, name: str, data: bytes, folder: str = ATT):
    path = vault.policy.root / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def code(exc: pytest.ExceptionInfo) -> str:
    return exc.value.code


@pytest.mark.parametrize(
    ("name", "data", "media"),
    [
        ("a.jpg", JPEG, "image/jpeg"),
        ("a.JPEG", JPEG, "image/jpeg"),
        ("b.png", PNG, "image/png"),
        ("c.gif", GIF, "image/gif"),
        ("d.webp", WEBP, "image/webp"),
        ("with space 1.png", PNG, "image/png"),
    ],
)
def test_reads_an_allowed_image_by_basename(vault, name, data, media):
    put(vault, name, data)
    out = vault.read_image(name)
    assert out["name"] == name and out["media_type"] == media and out["size"] == len(data)
    assert base64.b64decode(out["data_base64"]) == data


def test_finds_the_basename_in_a_subfolder_of_attachments_dir(vault):
    put(vault, "deep.png", PNG, folder=f"{ATT}/2026/01")
    assert vault.read_image("deep.png")["size"] == len(PNG)


@pytest.mark.parametrize(
    "name",
    [
        "../secret.png",
        "..",
        "sub/a.png",
        "/etc/passwd.png",
        "a\\b.png",
        "a\x00.png",
        "",
        "   ",
        ".hidden.png",
        "~/a.png",
        "C:a.png",
    ],
)
def test_a_name_with_path_segments_or_odd_characters_is_bad_name(vault, name):
    put(vault, "a.png", PNG)
    with pytest.raises(ImageRejected) as exc:
        vault.read_image(name)
    assert code(exc) == "bad_name"


@pytest.mark.parametrize("name", ["note.md", "a.svg", "a.pdf", "noext", "a.png.md", "a.exe"])
def test_other_extensions_are_bad_type_even_when_the_file_exists(vault, name):
    put(vault, name, PNG)
    with pytest.raises(ImageRejected) as exc:
        vault.read_image(name)
    assert code(exc) == "bad_type"


def test_content_that_is_not_the_claimed_image_type_is_bad_type(vault):
    put(vault, "fake.png", b"<html>not a png</html>")
    with pytest.raises(ImageRejected) as exc:
        vault.read_image("fake.png")
    assert code(exc) == "bad_type"


def test_a_missing_file_is_not_found(vault):
    with pytest.raises(ImageRejected) as exc:
        vault.read_image("gone.png")
    assert code(exc) == "not_found"


def test_a_missing_attachments_folder_is_not_found(tmp_path):
    with pytest.raises(ImageRejected) as exc:
        Vault(VaultPolicy(vault_dir=tmp_path)).read_image("a.png")
    assert code(exc) == "not_found"


def test_an_oversized_file_is_too_large(tmp_path):
    (tmp_path / ATT).mkdir(parents=True)
    vault = Vault(VaultPolicy(vault_dir=tmp_path, max_image_bytes=64))
    put(vault, "big.png", PNG + b"x" * 100)
    put(vault, "ok.png", PNG)
    with pytest.raises(ImageRejected) as exc:
        vault.read_image("big.png")
    assert code(exc) == "too_large"
    assert vault.read_image("ok.png")["size"] == len(PNG)


def test_the_default_limit_is_10_mib(tmp_path):
    assert VaultPolicy(vault_dir=tmp_path).max_image_bytes == 10 * 1024 * 1024


def test_a_symlinked_image_pointing_out_of_the_vault_is_refused(vault, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.png"
    outside.write_bytes(PNG)
    os.symlink(outside, vault.policy.root / ATT / "link.png")
    with pytest.raises(ImageRejected) as exc:
        vault.read_image("link.png")
    assert code(exc) == "bad_name"


def test_a_symlink_to_a_file_inside_the_vault_is_refused_too(vault):
    real = put(vault, "real.png", PNG, folder="0-INBOX")
    os.symlink(real, vault.policy.root / ATT / "inside.png")
    with pytest.raises(ImageRejected) as exc:
        vault.read_image("inside.png")
    assert code(exc) == "bad_name"


def test_a_symlinked_subfolder_is_not_followed(vault, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside-dir"
    outside.mkdir()
    (outside / "x.png").write_bytes(PNG)
    os.symlink(outside, vault.policy.root / ATT / "linked")
    with pytest.raises(ImageRejected) as exc:
        vault.read_image("x.png")
    assert code(exc) == "not_found"


def test_a_symlinked_attachments_folder_leaving_the_vault_is_refused(tmp_path):
    vault_dir = tmp_path / "vault"
    (vault_dir / "0-INBOX").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "a.png").write_bytes(PNG)
    os.symlink(outside, vault_dir / ATT)
    with pytest.raises((ImageRejected, PathRejected)):
        Vault(VaultPolicy(vault_dir=vault_dir)).read_image("a.png")


def test_an_image_outside_attachments_dir_is_not_found(vault):
    put(vault, "elsewhere.png", PNG, folder="0-INBOX")
    with pytest.raises(ImageRejected) as exc:
        vault.read_image("elsewhere.png")
    assert code(exc) == "not_found"


# -- the policy: own allowlist entry, derived from the layout (T001) -------------------------------------


def test_read_image_is_a_tool_with_its_own_allowlist_entry_from_the_layout(tmp_path):
    assert "read_image" in TOOLS
    assert VaultPolicy(vault_dir=tmp_path).allow["read_image"] == [ATT]
    layout = VaultLayout(attachments_dir="0-INBOX/pics")
    assert layout.default_allow()["read_image"] == ["0-INBOX/pics"]


def test_no_write_tool_gains_access_to_attachments_dir(tmp_path):
    """US3 scenario 3: the allowlist of the read tool names the folder; the write tools keep the PARA folders
    only, so a note written below attachments_dir through a write tool must be refused when the folder is not
    below a write folder."""
    layout = VaultLayout(attachments_dir="Attachments")
    policy = VaultPolicy(vault_dir=tmp_path, layout=layout)
    allow = policy.allow
    assert allow["read_image"] == ["Attachments"]
    for tool in ("write_note", "append_note", "move_note", "update_triage", "upsert_processing_list"):
        assert "Attachments" not in allow[tool]
    vault = Vault(policy)
    with pytest.raises(PathRejected):
        vault.write_note("Attachments/x.md", "x")


# -- T002: attachments_dir is read-only ---------------------------------------------------------------------


def test_attachments_dir_is_not_validated_as_a_write_destination():
    layout = VaultLayout(attachments_dir="Attachments")  # outside every write folder: fine, it is read only
    assert layout_structure_errors(layout) == []
    assert layout_structure_errors(DEFAULT_LAYOUT) == []


def test_attachments_dir_is_not_a_snapshot_root(tmp_path):
    layout = VaultLayout(attachments_dir="Attachments")
    assert "Attachments" not in layout.snapshot_roots()
    scope = derive_scope(VaultPolicy(vault_dir=tmp_path, layout=layout))
    assert "Attachments" not in scope.folders
    assert not scope.covers("Attachments/pic.png")


def test_read_image_is_read_only_for_the_snapshot_rules():
    from pkpipe.domain.vault_snapshot import READ_ONLY_VAULT_TOOLS, WRITING_VAULT_TOOLS

    assert "read_image" in READ_ONLY_VAULT_TOOLS and "read_image" not in WRITING_VAULT_TOOLS


def test_a_layout_path_for_attachments_must_be_allowed_for_read_image(tmp_path):
    from pkpipe.domain.layout import layout_errors

    layout = VaultLayout(attachments_dir="Attachments")
    errors = layout_errors(layout, {**layout.default_allow(), "read_image": ["0-INBOX"]})
    assert any("attachments_dir" in e and "read_image" in e for e in errors)


def test_a_decomposed_file_name_matches_the_composed_embed_name(vault):
    import unicodedata

    put(vault, unicodedata.normalize("NFD", "kép-é.png"), PNG)
    assert vault.read_image(unicodedata.normalize("NFC", "kép-é.png"))["size"] == len(PNG)
