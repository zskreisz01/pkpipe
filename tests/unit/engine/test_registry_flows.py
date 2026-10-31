"""Spec 005 T001/T007 on the layered config: flows/*.json, overlay merge, validation and pinned assets."""

from __future__ import annotations

import shutil

import pytest

from pkpipe.cli import main
from pkpipe.engine.registry import ConfigError, load_effective


@pytest.fixture
def pack(pack_dir, tmp_path):
    dest = tmp_path / "pack"
    shutil.copytree(pack_dir, dest)
    return dest


def test_pack_flows_load_and_validate(pack_dir):
    """SC-001 precondition: the public pack ships triage and process_released as data."""
    cfg = load_effective(pack_dir, None)
    flows = cfg.snapshot().flows()
    assert {"triage", "process_released", "categorize_item", "process_item"} <= set(flows)
    assert flows["triage"].node("categorize").for_each == "select_new.items"


def test_overlay_changes_one_node_of_a_flow_by_id(pack, overlay_dir, write_json):
    """Constitution III: an overlay patches a node's config by node id; other nodes stay."""
    write_json(overlay_dir, "flows/triage.json", {"nodes": [{"id": "notify", "config": {"event": "custom"}}]})
    flow = load_effective(pack, overlay_dir).snapshot().flow("triage")
    assert flow.node("notify").config["event"] == "custom"
    assert "message" in flow.node("notify").config and flow.node("list_inbox").type == "tool"


def test_unknown_step_type_in_a_pack_flow_fails_validation_naming_the_node(pack, write_json, capsys):
    """US1-AS2 via `pkpipe validate`."""
    write_json(
        pack, "flows/extra.json", {"id": "extra", "start": "x", "nodes": [{"id": "x", "type": "warp"}]}
    )
    with pytest.raises(ConfigError) as exc:
        load_effective(pack, None)
    assert any("flow 'extra': node 'x' has unknown step type 'warp'" in e for e in exc.value.errors)
    assert main(["validate", "--pack", str(pack)]) == 1
    assert "node 'x'" in capsys.readouterr().err


def test_flow_file_name_must_match_its_id(pack, write_json):
    write_json(
        pack, "flows/extra.json", {"id": "other", "start": "x", "nodes": [{"id": "x", "type": "code"}]}
    )
    with pytest.raises(ConfigError, match="must equal the file name"):
        load_effective(pack, None)


def test_version_covers_prompt_assets(pack):
    """T007: editing a prompt is a config change, so runs pin it and traces show a new version."""
    before = load_effective(pack, None)
    (pack / "prompts/system/triage.md").write_text("changed prompt\n", encoding="utf-8")
    after = load_effective(pack, None)
    assert before.version != after.version
    assert after.snapshot().asset("prompts/system/triage.md") == "changed prompt\n"
    assert before.snapshot().asset("prompts/system/triage.md") != "changed prompt\n"


def test_overlay_asset_wins_and_paths_cannot_escape(pack, overlay_dir):
    (overlay_dir / "prompts/system").mkdir(parents=True)
    (overlay_dir / "prompts/system/triage.md").write_text("private prompt", encoding="utf-8")
    snap = load_effective(pack, overlay_dir).snapshot()
    assert snap.asset("prompts/system/triage.md") == "private prompt"
    assert snap.asset("../pack/prompts/system/triage.md") is None
    assert snap.asset("/etc/passwd") is None


def test_snapshot_is_a_copy(pack_dir):
    cfg = load_effective(pack_dir, None)
    snap = cfg.snapshot()
    snap.data["flows"]["triage"]["nodes"].clear()
    assert cfg.data["flows"]["triage"]["nodes"]
