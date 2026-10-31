"""Layered config: core defaults -> public pack -> private overlay. Later layers win, key by key."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MERGE_KEYS = ("id", "flow")


def _keyed(items: list[Any]) -> str | None:
    """Return the merge key if every item is a dict carrying it (lists like schedules merge by flow)."""
    for key in MERGE_KEYS:
        if items and all(isinstance(i, dict) and key in i for i in items):
            return key
    return None


def deep_merge(base: dict[str, Any], top: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in top.items():
        current = out.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            out[key] = deep_merge(current, value)
        elif isinstance(value, list) and isinstance(current, list) and (k := _keyed(current + value)):
            merged = {item[k]: item for item in current}
            for item in value:
                merged[item[k]] = deep_merge(merged.get(item[k], {}), item)
            out[key] = list(merged.values())
        else:
            out[key] = value
    return out


def merge_categories(*layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Categories merge by id; an overlay can add, change or disable (enabled: false) a category."""
    by_id: dict[str, dict[str, Any]] = {}
    for layer in layers:
        for cat in layer:
            by_id[cat["id"]] = deep_merge(by_id.get(cat["id"], {}), cat)
    return [c for c in by_id.values() if c.get("enabled", True)]


def load_layer(directory: Path) -> dict[str, Any]:
    """Load every *.json file of a config directory into {stem: content}."""
    if not directory.exists():
        return {}
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))}


def load_config(pack_dir: Path, overlay_dir: Path | None = None) -> dict[str, Any]:
    pack = load_layer(pack_dir)
    overlay = load_layer(overlay_dir) if overlay_dir else {}
    merged = deep_merge({k: v for k, v in pack.items() if k != "categories"},
                       {k: v for k, v in overlay.items() if k != "categories"})
    merged["categories"] = merge_categories(pack.get("categories", []), overlay.get("categories", []))
    return merged
