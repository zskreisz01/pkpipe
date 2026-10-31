import json
from pathlib import Path

from pkpipe.engine.registry import load_config, merge_categories

PACK = Path(__file__).parent.parent / "packs" / "learning-inbox"


def test_public_pack_loads():
    cfg = load_config(PACK)
    ids = {c["id"] for c in cfg["categories"]}
    assert {"learning-material", "tool", "marketing", "misc"} <= ids


def test_overlay_adds_and_disables_categories(tmp_path):
    (tmp_path / "categories.json").write_text(json.dumps([
        {"id": "job", "label": "Jobs", "sessions": []},
        {"id": "marketing", "enabled": False},
    ]))
    ids = {c["id"] for c in load_config(PACK, tmp_path)["categories"]}
    assert "job" in ids and "marketing" not in ids


def test_overlay_schedules_are_added_not_replaced(tmp_path):
    (tmp_path / "schedules.json").write_text(json.dumps([{"cron": "30 21 * * *", "flow": "improvement_extract"}]))
    flows = {s["flow"] for s in load_config(PACK, tmp_path)["schedules"]}
    assert {"triage", "process_released", "improvement_extract"} <= flows


def test_merge_is_key_by_key():
    merged = merge_categories([{"id": "tool", "para_path": "a", "label": "T"}], [{"id": "tool", "para_path": "b"}])
    assert merged == [{"id": "tool", "para_path": "b", "label": "T"}]
