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


# -- spec 010: session plan validation of overlay categories --------------------------------------------
def _plan_category(*sessions, **extra):
    return {**PRIVATE_CATEGORY, "id": "plan", "sessions": list(sessions), **extra}


def _session(id, **kw):
    return {"id": id, "system": "analyst", "instruction": "job/fit.md", "inputs": ["item.extract"], **kw}


def test_overlay_category_with_a_plan_of_its_own_validates_and_names_its_note_session(
    pack_dir, overlay, write_json
):
    """SC-002: a new category is configuration only, with sessions, tools, context and note session."""
    cat = _plan_category(
        _session("research", tools=["websearch:search"], output_schema="tool_research"),
        _session("fit", inputs=["research"], context="personal", output_schema="tool_fit"),
        _session("late", inputs=["fit"]),
        note_session="fit",
    )
    write_json(overlay, "categories.json", [cat])
    cfg = load_effective(pack_dir, overlay)
    effective = next(c for c in cfg.data["categories"] if c["id"] == "plan")
    assert effective["note_session"] == "fit" and [s["id"] for s in effective["sessions"]] == [
        "research",
        "fit",
        "late",
    ]


@pytest.mark.parametrize(
    ("sessions", "message"),
    [
        (
            [_session("r", inputs=["item.text"], tools=["websearch:search"])],
            "raw untrusted input 'item.text'",
        ),
        ([_session("r", inputs=["*"], tools=["websearch:search"])], "raw untrusted input '\\*'"),
        (
            [_session("e", context="enterprise", tools=["websearch:search"])],
            "enterprise context must not have web search tools",
        ),
        (
            [
                _session("e", context="enterprise", output_schema="tool_fit"),
                _session("w", inputs=["e"], tools=["websearch:search"]),
            ],
            "enterprise-derived",
        ),
        ([_session("a", inputs=["later"]), _session("later")], "input 'later'"),
    ],
)
def test_overlay_plan_rule_violations_fail_validation(pack_dir, overlay, write_json, sessions, message):
    write_json(overlay, "categories.json", [_plan_category(*sessions)])
    with pytest.raises(ConfigError, match=message):
        load_effective(pack_dir, overlay)


def test_overlay_instruction_over_the_token_budget_fails_validation(pack_dir, overlay, write_json):
    """FR-003: about 800 tokens at most."""
    (overlay / "instructions/job/fit.md").write_text("word " * 700, encoding="utf-8")
    write_json(overlay, "categories.json", [_plan_category(_session("a"))])
    with pytest.raises(ConfigError, match="FR-003"):
        load_effective(pack_dir, overlay)


def test_overlay_query_guard_terms_are_validated_config(pack_dir, overlay, write_json):
    write_json(
        overlay,
        "security.json",
        {"query_guard": {"blocked_terms": ["Project Falcon"], "terms_file": "contexts/t.txt"}},
    )
    cfg = load_effective(pack_dir, overlay)
    assert cfg.data["security"]["query_guard"]["blocked_terms"] == ["Project Falcon"]
    write_json(overlay, "security.json", {"query_guard": {"blocked": ["x"]}})
    with pytest.raises(ConfigError, match="query_guard"):
        load_effective(pack_dir, overlay)
