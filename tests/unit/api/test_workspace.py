"""Spec 014 FR-003: which paths a draft may touch, hashing and diffs (no server needed)."""

from __future__ import annotations

import pytest

from pkpipe.api.workspace import ABSENT, ConfigWorkspace, WorkspaceError, content_hash, editable_path


@pytest.mark.parametrize(
    "path",
    [
        "categories.json",
        "models.json",
        "evals.json",
        "flows/triage.json",
        "instructions/tool/research.md",
        "prompts/system/analyst.md",
        "contexts/personal.md",
        "schemas/triage_result.json",
        "templates/triage_ready.discord_webhook.md",
    ],
)
def test_config_paths_are_editable(path):
    assert editable_path(path) == path


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/etc/passwd",
        "../x.md",
        "instructions/../../x.md",
        ".git/config",
        "instructions/.hidden.md",
        "other.json",
        "flows/x.md",
        "flows/a/b.json",
        "flows/.json",
        "prompts/run.sh",
        "evals/datasets/x.jsonl",
        "instructions\\\\x.md",
        "C:/x.json",
        "pack/../categories.json",
    ],
)
def test_everything_else_is_refused(path):
    with pytest.raises(WorkspaceError):
        editable_path(path)


def test_redundant_slashes_are_normalised_not_trusted():
    assert editable_path("instructions//x.md") == "instructions/x.md"


def test_hash_of_a_missing_file_is_absent_and_text_hashes_differ():
    assert content_hash(None) == ABSENT
    assert content_hash("a") != content_hash("b") and content_hash("a") == content_hash("a")


def test_with_file_needs_an_overlay_and_keeps_the_first_base_hash(tmp_path, pack_dir):
    read_only = ConfigWorkspace(pack_dir, None)
    with pytest.raises(WorkspaceError, match="read-only"):
        read_only.with_file({}, "categories.json", "[]", None)
    overlay = tmp_path / "ov"
    overlay.mkdir()
    ws = ConfigWorkspace(pack_dir, overlay)
    first = ws.with_file({}, "telemetry.json", "{}", ABSENT)
    again = ws.with_file(first, "telemetry.json", '{"capture_content": {}}', "ignored-by-design")
    assert again["telemetry.json"]["base_hash"] == ABSENT  # the hash the draft started from is kept
    deleted = ws.with_file(again, "telemetry.json", None, None)
    assert deleted["telemetry.json"]["content"] is None


def test_diff_marks_new_changed_and_deleted_files(tmp_path, pack_dir):
    overlay = tmp_path / "ov"
    (overlay / "instructions").mkdir(parents=True)
    (overlay / "instructions" / "a.md").write_text("one\ntwo\n")
    ws = ConfigWorkspace(pack_dir, overlay)
    files = {
        "instructions/a.md": {"content": "one\nthree\n", "base_hash": content_hash("one\ntwo\n")},
        "instructions/new.md": {"content": "fresh\n", "base_hash": ABSENT},
        "instructions/same.md": {"content": None, "base_hash": ABSENT},  # deleting a file that is not there
    }
    diffs = {d["path"]: d["diff"] for d in ws.diff(files)}
    assert "-two" in diffs["instructions/a.md"] and "+three" in diffs["instructions/a.md"]
    assert "+fresh" in diffs["instructions/new.md"] and "same.md" not in diffs
