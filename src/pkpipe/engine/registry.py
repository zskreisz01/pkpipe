"""Layered config: core defaults -> public pack -> private overlay. Later layers win, key by key (spec 001).

Merge rules
-----------
* dicts merge key by key, recursively;
* lists of objects merge by ``id`` (categories, sessions) or ``flow`` (schedules); an overlay entry with an
  existing key is merged into it, a new key is appended;
* ``enabled: false`` on a merged category or schedule removes it from the effective config;
* any other value (scalars, plain lists, lists of objects without a key) is replaced by the later layer.

The effective config carries a sha256 version hash of its canonical JSON; flows record it in run state.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pkpipe.engine.config_models import CONFIG_MODELS, validate_effective

MERGE_KEYS = ("id", "flow")
OVERLAY_ENV = "PKPIPE_OVERLAY_DIR"
PACK_ENV = "PKPIPE_PACK_DIR"
#: config files whose top level is a list of keyed objects
LIST_FILES = {"categories": "id", "schedules": "flow"}

#: Core defaults: the first layer under every pack. Safe-by-default security values (constitution IV) and
#: empty routing, so a pack only has to state what it changes. Packs and overlays override key by key.
CORE_DEFAULTS: dict[str, Any] = {
    "security": {
        "canary": {"rotate": "per_run"},
        "url_filter": {"block_private_networks": True, "max_bytes": 2_000_000},
        "quarantine_dir": "0-INBOX/_quarantine",
    },
    "channels": {"channels": {}, "routes": {}},
    "telemetry": {"capture_content": {"flows": []}},
}


class ConfigError(Exception):
    """Raised for unreadable or structurally invalid config; ``errors`` names file and problem."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


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
            out[key] = merge_keyed(current, value, k)
        else:
            out[key] = value
    return out


def merge_keyed(base: list[dict[str, Any]], top: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    merged = {item[key]: item for item in base}
    for item in top:
        merged[item[key]] = deep_merge(merged.get(item[key], {}), item)
    return list(merged.values())


def merge_categories(*layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Categories merge by id; an overlay can add, change or disable (enabled: false) a category."""
    by_id: dict[str, dict[str, Any]] = {}
    for layer in layers:
        for cat in layer:
            by_id[cat["id"]] = deep_merge(by_id.get(cat["id"], {}), cat)
    return [c for c in by_id.values() if c.get("enabled", True)]


def _check_layer_shape(path: Path, stem: str, content: Any) -> None:
    expected_list = stem in LIST_FILES
    if expected_list and not isinstance(content, list):
        raise ConfigError([f"{path}: expected a JSON list"])
    if stem in CONFIG_MODELS and not expected_list and not isinstance(content, dict):
        raise ConfigError([f"{path}: expected a JSON object"])
    if expected_list:
        key = LIST_FILES[stem]
        seen: set[Any] = set()
        for entry in content:
            if not isinstance(entry, dict) or key not in entry:
                raise ConfigError([f"{path}: every entry needs an '{key}'"])
            if entry[key] in seen:
                raise ConfigError([f"{path}: duplicate {key} '{entry[key]}'"])
            seen.add(entry[key])


def load_layer(directory: Path) -> dict[str, Any]:
    """Load every *.json file of a config directory into {stem: content}. A missing directory is empty."""
    if not directory or not directory.is_dir():
        return {}
    out: dict[str, Any] = {}
    for p in sorted(directory.glob("*.json")):
        try:
            content = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError([f"{p}: invalid JSON ({exc.msg} at line {exc.lineno})"]) from exc
        _check_layer_shape(p, p.stem, content)
        out[p.stem] = content
    return out


def merge_layers(*layers: Mapping[str, Any]) -> dict[str, Any]:
    """Merge already loaded layers (core defaults, pack, overlay, ...) into one effective dict."""
    merged: dict[str, Any] = {}
    for layer in layers:
        for stem, content in layer.items():
            if stem in LIST_FILES and isinstance(content, list):
                merged[stem] = merge_keyed(merged.get(stem, []), content, LIST_FILES[stem])
            elif isinstance(content, dict) and isinstance(merged.get(stem), dict):
                merged[stem] = deep_merge(merged[stem], content)
            else:
                merged[stem] = content
    for stem in LIST_FILES:  # enabled: false removes the entry
        if stem in merged:
            merged[stem] = [e for e in merged[stem] if e.get("enabled", True)]
    return merged


def config_version(data: Mapping[str, Any]) -> str:
    """sha256 over the canonical JSON (sorted keys, no whitespace) of the effective config."""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def default_pack_dir() -> Path:
    env = os.environ.get(PACK_ENV)
    return Path(env) if env else Path(__file__).resolve().parents[3] / "packs" / "learning-inbox"


def overlay_dir_from_env(environ: Mapping[str, str] | None = None) -> Path | None:
    value = (environ if environ is not None else os.environ).get(OVERLAY_ENV)
    return Path(value).expanduser() if value else None


@dataclass
class EffectiveConfig:
    data: dict[str, Any]
    version: str
    pack_dir: Path
    overlay_dir: Path | None = None
    warnings: list[str] = field(default_factory=list)

    def find_file(self, relative: str) -> Path | None:
        """Resolve a Markdown/JSON asset (prompt, instruction, context, schema): overlay first, then pack.

        Paths that are absolute or escape their layer directory (``..``) never resolve.
        """
        if Path(relative).is_absolute():
            return None
        for base in (self.overlay_dir, self.pack_dir):
            if not base:
                continue
            candidate = base / relative
            if candidate.resolve().is_relative_to(base.resolve()) and candidate.is_file():
                return candidate
        return None


def load_config(pack_dir: Path, overlay_dir: Path | None = None) -> dict[str, Any]:
    """Core defaults -> pack -> overlay, merged (no model validation, see :func:`load_effective`)."""
    return merge_layers(
        copy.deepcopy(CORE_DEFAULTS), load_layer(pack_dir), load_layer(overlay_dir) if overlay_dir else {}
    )


def check_references(cfg: EffectiveConfig) -> tuple[list[str], list[str]]:
    """Cross-file checks: referenced prompts, instructions, schemas, tools. Returns (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    servers = cfg.data.get("mcp_servers", {})
    for cat in cfg.data.get("categories", []):
        for s in cat.get("sessions", []):
            where = f"category '{cat['id']}' session '{s['id']}'"
            for rel, what in (
                (f"prompts/system/{s['system']}.md", "system prompt"),
                (f"instructions/{s['instruction']}", "instruction"),
            ):
                if cfg.find_file(rel) is None:
                    errors.append(f"{where}: {what} '{rel}' not found")
            if s.get("output_schema") and cfg.find_file(f"schemas/{s['output_schema']}.json") is None:
                errors.append(f"{where}: output schema '{s['output_schema']}' not found")
            if s.get("context") and cfg.find_file(f"contexts/{s['context']}.md") is None:
                warnings.append(f"{where}: context pack '{s['context']}' not present (private overlay?)")
            for tool in s.get("tools", []):
                server, _, name = tool.partition(":")
                if server not in servers:
                    errors.append(f"{where}: tool '{tool}' references unregistered MCP server '{server}'")
                elif name not in servers[server].get("allow", []):
                    errors.append(f"{where}: tool '{tool}' is not in the allow list of '{server}'")
    deny = cfg.data.get("security", {}).get("url_filter", {}).get("deny_list")
    if deny and cfg.find_file(deny) is None:
        warnings.append(f"security.url_filter.deny_list '{deny}' not found")
    return errors, warnings


def load_effective(
    pack_dir: Path | None = None, overlay_dir: Path | None = None, *, strict: bool = True
) -> EffectiveConfig:
    """Load pack + optional overlay, validate shape and references, compute the version hash.

    ``overlay_dir=None`` falls back to ``PKPIPE_OVERLAY_DIR``. Raises :class:`ConfigError` when ``strict``.
    """
    pack_dir = pack_dir or default_pack_dir()
    overlay_dir = overlay_dir if overlay_dir is not None else overlay_dir_from_env()
    warnings: list[str] = []
    if overlay_dir is not None and not overlay_dir.is_dir():
        warnings.append(f"overlay directory '{overlay_dir}' does not exist; using the pack only")
        overlay_dir = None
    if not pack_dir.is_dir():
        raise ConfigError([f"pack directory '{pack_dir}' does not exist"])
    data = load_config(pack_dir, overlay_dir)
    cfg = EffectiveConfig(data, config_version(data), pack_dir, overlay_dir, warnings)
    errors = validate_effective(data)
    ref_errors, ref_warnings = check_references(cfg) if not errors else ([], [])
    cfg.warnings.extend(ref_warnings)
    errors += ref_errors
    if errors and strict:
        raise ConfigError(errors)
    return cfg
