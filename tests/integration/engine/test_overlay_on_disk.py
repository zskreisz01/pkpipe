"""Spec 001 US2 / SC-003 against real directories: a private overlay with its own files, no code change."""

from __future__ import annotations

import shutil

import pytest

from pkpipe.engine.registry import ConfigError, load_effective

PRIVATE_CATEGORY = {
    "id": "job",
    "label": "Jobs",
    "subflow": "other_resource",
    "para_path": "1-Projects/Jobs",
    "triage_hint": "Synthetic job offers.",
    "sessions": [
        {
            "id": "fit",
            "system": "analyst",
            "instruction": "job/fit.md",
            "context": "personal",
            "inputs": ["item.extract"],
        },
    ],
}


@pytest.fixture
def overlay(overlay_dir):
    (overlay_dir / "instructions" / "job").mkdir(parents=True)
    (overlay_dir / "instructions" / "job" / "fit.md").write_text("Rate the fit.\n", encoding="utf-8")
    (overlay_dir / "contexts").mkdir()
    (overlay_dir / "contexts" / "personal.md").write_text("Synthetic personal context.\n", encoding="utf-8")
    return overlay_dir


def test_overlay_with_private_category_instruction_and_context_validates(pack_dir, overlay, write_json):
    write_json(overlay, "categories.json", [PRIVATE_CATEGORY])
    cfg = load_effective(pack_dir, overlay)
    assert not [w for w in cfg.warnings if "personal" in w], "overlay supplies the personal context pack"
    assert cfg.find_file("contexts/personal.md") == overlay / "contexts" / "personal.md"
    assert cfg.find_file("prompts/system/analyst.md") == pack_dir / "prompts/system/analyst.md"


def test_overlay_category_referencing_missing_instruction_fails_validation(pack_dir, overlay, write_json):
    cat = {
        **PRIVATE_CATEGORY,
        "sessions": [{**PRIVATE_CATEGORY["sessions"][0], "instruction": "job/missing.md"}],
    }
    write_json(overlay, "categories.json", [cat])
    with pytest.raises(ConfigError, match=r"job/missing\.md"):
        load_effective(pack_dir, overlay)


def test_overlay_cannot_grant_a_tool_the_mcp_registration_does_not_allow(pack_dir, overlay, write_json):
    """Constitution IV: tool grants are bounded by the registration, even for private config."""
    cat = {
        **PRIVATE_CATEGORY,
        "sessions": [{**PRIVATE_CATEGORY["sessions"][0], "tools": ["obsidian:delete_note"]}],
    }
    write_json(overlay, "categories.json", [cat])
    with pytest.raises(ConfigError, match="allow list of 'obsidian'"):
        load_effective(pack_dir, overlay)


def test_effective_version_is_stable_across_copies_of_the_same_content(pack_dir, tmp_path):
    copy = tmp_path / "pack-copy"
    shutil.copytree(pack_dir, copy)
    assert load_effective(copy).version == load_effective(pack_dir).version


def test_all_validation_errors_are_collected_not_just_the_first(pack_dir, overlay, write_json):
    write_json(overlay, "schedules.json", [{"flow": "triage", "cron": "bad"}])
    write_json(overlay, "security.json", {"canary": {"rotate": "sometimes"}})
    with pytest.raises(ConfigError) as exc:
        load_effective(pack_dir, overlay)
    files = {e.split(":")[0] for e in exc.value.errors}
    assert {"schedules.json", "security.json"} <= files
