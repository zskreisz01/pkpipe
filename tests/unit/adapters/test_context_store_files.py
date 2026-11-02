"""Spec 011: the file adapter of ContextStorePort writes only below ``contexts/`` and ``security/`` of the
overlay, atomically."""

from __future__ import annotations

import pytest

from pkpipe.adapters.context_store_files import FileContextStore


@pytest.fixture
def store(tmp_path):
    return FileContextStore(tmp_path / "overlay")


def test_write_read_and_remove_roundtrip(store, tmp_path):
    assert store.read("contexts/personal.md") is None
    store.write("contexts/personal.md", "hello ü\n")
    assert store.read("contexts/personal.md") == "hello ü\n"
    assert (tmp_path / "overlay/contexts/personal.md").read_text(encoding="utf-8") == "hello ü\n"
    store.remove("contexts/personal.md")
    assert store.read("contexts/personal.md") is None
    store.remove("contexts/personal.md")  # a missing file is no error


def test_a_rewrite_replaces_the_file_and_leaves_no_temp_files(store, tmp_path):
    store.write("security/query_guard_terms.txt", "a\n")
    store.write("security/query_guard_terms.txt", "b\n")
    assert store.read("security/query_guard_terms.txt") == "b\n"
    assert [p.name for p in (tmp_path / "overlay/security").iterdir()] == ["query_guard_terms.txt"]


@pytest.mark.parametrize(
    "path",
    [
        "../x.md",
        "/etc/passwd",
        "contexts/../../x",
        "flows/context_refresh.json",
        "contexts",
        "security",
        "contexts\\x.md",
        "",
        "prompts/system/x.md",
    ],
)
def test_paths_outside_contexts_and_security_are_refused(store, tmp_path, path):
    for call in (lambda: store.read(path), lambda: store.write(path, "x"), lambda: store.remove(path)):
        with pytest.raises(ValueError, match="not a context store path"):
            call()
    assert not (tmp_path / "overlay").exists() or not any((tmp_path / "overlay").rglob("*.*"))


def test_a_symlinked_contexts_folder_is_not_followed_out_of_the_overlay_by_name_tricks(store, tmp_path):
    """Only the relative path is validated; the overlay is the owner's own directory, so a symlink they
    made is theirs. A path component cannot add one."""
    with pytest.raises(ValueError):
        store.write("contexts/../security/x.txt", "x")
