"""Spec 001 US2 / FR-002 / T004 / T006: layered config merge rules and the effective-config hash."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pkpipe.engine.registry import (
    ConfigError,
    config_version,
    deep_merge,
    load_config,
    load_effective,
    load_layer,
    merge_categories,
    merge_layers,
    overlay_dir_from_env,
)

CAT = {
    "id": "job",
    "label": "Jobs",
    "subflow": "other_resource",
    "para_path": "3-Resources/Jobs",
    "triage_hint": "Job offers.",
    "sessions": [],
}


# --- US2 acceptance scenarios -------------------------------------------------------------------------
def test_public_pack_loads(pack_dir):
    cfg = load_config(pack_dir)
    assert {"learning-material", "tool", "marketing", "misc"} <= {c["id"] for c in cfg["categories"]}


def test_overlay_category_with_existing_id_merges_key_by_key(pack_dir, overlay_dir, write_json):
    """US2.1: fields merge key by key, untouched fields survive."""
    write_json(overlay_dir, "categories.json", [{"id": "tool", "para_path": "3-Resources/MyTools"}])
    tool = next(c for c in load_config(pack_dir, overlay_dir)["categories"] if c["id"] == "tool")
    assert tool["para_path"] == "3-Resources/MyTools"
    assert tool["label"] == "Tools" and len(tool["sessions"]) == 6


def test_enabled_false_removes_category(pack_dir, overlay_dir, write_json):
    """US2.2"""
    write_json(overlay_dir, "categories.json", [{"id": "marketing", "enabled": False}, CAT])
    ids = {c["id"] for c in load_config(pack_dir, overlay_dir)["categories"]}
    assert "marketing" not in ids and "job" in ids


def test_overlay_schedules_are_added_not_replaced(pack_dir, overlay_dir, write_json):
    """US2.3"""
    write_json(overlay_dir, "schedules.json", [{"cron": "30 21 * * *", "flow": "improvement_extract"}])
    flows = {s["flow"] for s in load_config(pack_dir, overlay_dir)["schedules"]}
    assert {"triage", "process_released", "context_refresh", "improvement_extract"} <= flows


def test_overlay_schedule_with_existing_flow_changes_only_given_fields(pack_dir, overlay_dir, write_json):
    write_json(overlay_dir, "schedules.json", [{"flow": "triage", "cron": "0 21 * * *"}])
    sched = {s["flow"]: s for s in load_config(pack_dir, overlay_dir)["schedules"]}
    assert sched["triage"]["cron"] == "0 21 * * *"
    assert sched["process_released"]["cron"] == "0 6 * * *"


def test_overlay_can_disable_a_schedule(pack_dir, overlay_dir, write_json):
    write_json(overlay_dir, "schedules.json", [{"flow": "context_refresh", "enabled": False}])
    assert "context_refresh" not in {s["flow"] for s in load_config(pack_dir, overlay_dir)["schedules"]}


# --- merge rules ----------------------------------------------------------------------------------------
def test_dict_merge_is_recursive_and_does_not_mutate_inputs():
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    top = {"a": {"y": 3}}
    assert deep_merge(base, top) == {"a": {"x": 1, "y": 3}, "b": 1}
    assert base == {"a": {"x": 1, "y": 2}, "b": 1} and top == {"a": {"y": 3}}


def test_sessions_inside_a_category_merge_by_id():
    base = [
        {"id": "tool", "sessions": [{"id": "research", "tools": ["websearch:search"]}, {"id": "compare"}]}
    ]
    top = [{"id": "tool", "sessions": [{"id": "research", "tools": []}, {"id": "extra"}]}]
    sessions = merge_categories(base, top)[0]["sessions"]
    assert [s["id"] for s in sessions] == ["research", "compare", "extra"]
    assert sessions[0]["tools"] == []


def test_plain_lists_and_scalars_are_replaced_not_concatenated():
    assert deep_merge({"block_on": ["Unsafe"], "t": 1}, {"block_on": ["Controversial"], "t": 2}) == {
        "block_on": ["Controversial"],
        "t": 2,
    }


def test_merge_key_by_key_for_categories():
    merged = merge_categories(
        [{"id": "tool", "para_path": "a", "label": "T"}], [{"id": "tool", "para_path": "b"}]
    )
    assert merged == [{"id": "tool", "para_path": "b", "label": "T"}]


def test_later_layer_wins_over_earlier_for_three_layers():
    core = {"models": {"default": {"temperature": 0.1, "context": 1000}}}
    pack = {"models": {"default": {"temperature": 0.2}}}
    overlay = {"models": {"default": {"context": 2000}, "judge": {"temperature": 0.0}}}
    eff = merge_layers(core, pack, overlay)
    assert eff["models"] == {"default": {"temperature": 0.2, "context": 2000}, "judge": {"temperature": 0.0}}


def test_overlay_cannot_remove_a_pack_file_by_omission(pack_dir, overlay_dir, write_json):
    write_json(overlay_dir, "models.json", {"default": {"temperature": 0.9}})
    cfg = load_config(pack_dir, overlay_dir)
    assert cfg["models"]["default"]["temperature"] == 0.9
    assert cfg["models"]["judge"]["temperature"] == 0.0 and "security" in cfg and "schedules" in cfg


# --- edge cases -------------------------------------------------------------------------------------------
def test_missing_overlay_directory_loads_pack_only(pack_dir, tmp_path):
    cfg = load_effective(pack_dir, tmp_path / "does-not-exist")
    assert cfg.overlay_dir is None
    assert any("does not exist" in w for w in cfg.warnings)
    assert cfg.data == load_config(pack_dir)


def test_duplicate_category_ids_in_one_layer_name_the_file(overlay_dir, write_json):
    path = write_json(overlay_dir, "categories.json", [CAT, CAT])
    with pytest.raises(ConfigError) as exc:
        load_layer(overlay_dir)
    assert str(path) in exc.value.errors[0] and "duplicate id 'job'" in exc.value.errors[0]


def test_duplicate_schedule_flows_in_one_layer_name_the_file(overlay_dir, write_json):
    path = write_json(overlay_dir, "schedules.json", [{"cron": "0 1 * * *", "flow": "x"}] * 2)
    with pytest.raises(ConfigError, match="duplicate flow 'x'") as exc:
        load_layer(overlay_dir)
    assert str(path) in exc.value.errors[0]


def test_invalid_json_names_file_and_line(overlay_dir):
    (overlay_dir / "models.json").write_text('{"default": \n{oops}', encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_layer(overlay_dir)
    assert "models.json" in exc.value.errors[0] and "line" in exc.value.errors[0]


def test_wrong_top_level_type_is_rejected(overlay_dir, write_json):
    write_json(overlay_dir, "categories.json", {"id": "oops"})
    with pytest.raises(ConfigError, match="expected a JSON list"):
        load_layer(overlay_dir)


def test_overlay_dir_from_env():
    assert overlay_dir_from_env({"PKPIPE_OVERLAY_DIR": "/x/y"}) == Path("/x/y")
    assert overlay_dir_from_env({}) is None
    assert overlay_dir_from_env({"PKPIPE_OVERLAY_DIR": ""}) is None


def test_load_effective_reads_overlay_from_env(pack_dir, overlay_dir, write_json, monkeypatch):
    """FR-002: PKPIPE_OVERLAY_DIR selects the overlay."""
    write_json(overlay_dir, "categories.json", [CAT])
    monkeypatch.setenv("PKPIPE_OVERLAY_DIR", str(overlay_dir))
    assert "job" in {c["id"] for c in load_effective(pack_dir).data["categories"]}


def test_adding_overlay_category_needs_no_code_change(pack_dir, overlay_dir, write_json):
    """SC-003: a complete overlay category validates and is part of the effective config."""
    write_json(overlay_dir, "categories.json", [CAT])
    cfg = load_effective(pack_dir, overlay_dir)
    assert "job" in {c["id"] for c in cfg.data["categories"]}


# --- T004 version hash ------------------------------------------------------------------------------------
def test_version_is_sha256_and_independent_of_key_order():
    a = {"x": 1, "y": {"b": 1, "a": 2}}
    b = {"y": {"a": 2, "b": 1}, "x": 1}
    assert config_version(a) == config_version(b)
    assert len(config_version(a)) == 64 and int(config_version(a), 16) >= 0


def test_version_changes_with_any_effective_change(pack_dir, overlay_dir, write_json):
    base = load_effective(pack_dir).version
    write_json(overlay_dir, "schedules.json", [{"flow": "triage", "cron": "0 21 * * *"}])
    changed = load_effective(pack_dir, overlay_dir).version
    assert changed != base
    assert load_effective(pack_dir, overlay_dir).version == changed  # deterministic


def test_noop_overlay_keeps_version(pack_dir, overlay_dir, write_json):
    write_json(overlay_dir, "models.json", {"default": {"temperature": 0.2}})  # same value as the pack
    assert load_effective(pack_dir, overlay_dir).version == load_effective(pack_dir).version


# --- reference validation ---------------------------------------------------------------------------------
@pytest.fixture
def pack_copy(pack_dir, tmp_path) -> Path:
    dest = tmp_path / "pack"
    shutil.copytree(pack_dir, dest)
    return dest


def test_pack_validates_with_only_warnings(pack_dir):
    cfg = load_effective(pack_dir)
    assert any("personal" in w for w in cfg.warnings)  # context packs live in the private overlay


def test_missing_instruction_file_is_an_error(pack_copy):
    (pack_copy / "instructions" / "tool" / "compare.md").unlink()
    with pytest.raises(ConfigError, match="instructions/tool/compare.md"):
        load_effective(pack_copy)


def test_missing_system_prompt_is_an_error(pack_copy):
    (pack_copy / "prompts" / "system" / "analyst.md").unlink()
    with pytest.raises(ConfigError, match="prompts/system/analyst.md"):
        load_effective(pack_copy)


def test_unknown_output_schema_is_an_error(pack_copy):
    (pack_copy / "schemas" / "tool_evaluation.json").unlink()
    with pytest.raises(ConfigError, match="output schema 'tool_evaluation'"):
        load_effective(pack_copy)


def test_tool_outside_allow_list_is_an_error(pack_copy):
    """Constitution IV: sessions can only be granted tools the MCP registration allows."""
    path = pack_copy / "mcp_servers.json"
    cfg = json.loads(path.read_text())
    cfg["websearch"]["allow"] = []
    path.write_text(json.dumps(cfg))
    with pytest.raises(ConfigError, match="not in the allow list of 'websearch'"):
        load_effective(pack_copy)


def test_tool_of_unregistered_server_is_an_error(pack_copy):
    path = pack_copy / "mcp_servers.json"
    cfg = json.loads(path.read_text())
    del cfg["learning"]
    path.write_text(json.dumps(cfg))
    with pytest.raises(ConfigError, match="unregistered MCP server 'learning'"):
        load_effective(pack_copy)


def test_overlay_instruction_file_shadows_pack_file(pack_dir, overlay_dir):
    target = overlay_dir / "instructions" / "tool" / "compare.md"
    target.parent.mkdir(parents=True)
    target.write_text("private", encoding="utf-8")
    cfg = load_effective(pack_dir, overlay_dir)
    assert cfg.find_file("instructions/tool/compare.md") == target
    assert cfg.find_file("instructions/tool/research.md") == pack_dir / "instructions/tool/research.md"
    assert cfg.find_file("nope.md") is None


def test_non_strict_returns_config_with_errors_unraised(pack_copy):
    (pack_copy / "prompts" / "system" / "analyst.md").unlink()
    assert load_effective(pack_copy, strict=False).data["categories"]


def test_missing_pack_directory_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="pack directory"):
        load_effective(tmp_path / "nope")


# --- FR-002 core defaults layer ---------------------------------------------------------------------------
def test_core_defaults_are_the_first_layer_and_the_pack_overrides_them(tmp_path, write_json):
    """FR-002: core defaults -> pack -> overlay. A pack security.json with only the guards still gets the
    safe defaults (private networks blocked, per-run canary, quarantine dir); a pack value wins."""
    pack = tmp_path / "pack"
    write_json(
        pack,
        "security.json",
        {
            "classifier": {"backend": "qwen3guard", "threshold": 0.5},
            "output_guard": {"block_on": ["Unsafe"]},
            "url_filter": {"max_bytes": 1000},
        },
    )
    cfg = load_effective(pack)
    sec = cfg.data["security"]
    assert sec["url_filter"] == {"block_private_networks": True, "max_bytes": 1000}
    assert sec["canary"] == {"rotate": "per_run"} and "quarantine_dir" not in sec
    assert cfg.data["channels"] == {"channels": {}, "routes": {}}


def test_core_defaults_are_not_mutated_by_loading(tmp_path, write_json):
    from pkpipe.engine.registry import CORE_DEFAULTS

    before = json.dumps(CORE_DEFAULTS, sort_keys=True)
    pack = tmp_path / "pack"
    write_json(pack, "security.json", {"url_filter": {"block_private_networks": False, "max_bytes": 5}})
    cfg = load_config(pack)
    cfg["security"]["canary"]["rotate"] = "never"
    assert json.dumps(CORE_DEFAULTS, sort_keys=True) == before


def test_overlay_can_override_a_core_default(pack_dir, overlay_dir, write_json):
    write_json(overlay_dir, "security.json", {"canary": {"rotate": "daily"}})
    assert load_effective(pack_dir, overlay_dir).data["security"]["canary"] == {"rotate": "daily"}


# --- references cannot escape their layer -----------------------------------------------------------------
@pytest.mark.parametrize(
    "field,value",
    [
        ("instruction", "../../secrets/notes.md"),
        ("instruction", "/etc/passwd"),
        ("system", "../analyst"),
        ("context", "../../personal"),
        ("output_schema", "C:\\\\x"),
    ],
)
def test_session_references_outside_the_layer_are_rejected(pack_dir, overlay_dir, write_json, field, value):
    """Public/private separation (constitution VII): a session can only reference files inside a layer."""
    session = {"id": "s", "system": "analyst", "instruction": "tool/fit.md", field: value}
    write_json(overlay_dir, "categories.json", [{**CAT, "sessions": [session]}])
    with pytest.raises(ConfigError, match="relative path inside the pack"):
        load_effective(pack_dir, overlay_dir)


def test_find_file_never_resolves_outside_the_layer(pack_dir, overlay_dir, tmp_path):
    (tmp_path / "outside.md").write_text("x", encoding="utf-8")
    cfg = load_effective(pack_dir, overlay_dir)
    assert cfg.find_file("../outside.md") is None
    assert cfg.find_file(str(tmp_path / "outside.md")) is None
