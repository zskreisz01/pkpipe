"""Layered config: core defaults -> public pack -> private overlay. Later layers win, key by key (spec 001).

Merge rules
-----------
* dicts merge key by key, recursively;
* lists of objects merge by ``id`` (categories, sessions) or ``flow`` (schedules); an overlay entry with an
  existing key is merged into it, a new key is appended;
* ``enabled: false`` on a merged category or schedule removes it from the effective config;
* any other value (scalars, plain lists, lists of objects without a key) is replaced by the later layer.

Flows live in ``flows/<id>.json`` and are merged per flow id like any other dict (nodes merge by ``id``).

The effective config carries a sha256 version hash over its canonical JSON and its text assets (prompts,
instructions, context packs, schemas); runs pin it (spec 005) and every trace records it.
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

from pkpipe.domain.layout import DEFAULT_LAYOUT
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
    },
    #: spec 020: every vault folder and file the flows and steps use (``${config.layout.<name>}``)
    "layout": DEFAULT_LAYOUT.model_dump(),
    #: spec 019: retention of the vault snapshots taken before a writing run
    "snapshots": {"retention_days": 14, "max_bytes": 1024**3, "max_file_bytes": 5 * 1024**2},
    "channels": {"channels": {}, "routes": {}},
    "telemetry": {"capture_content": {"flows": []}},
    # Code-only levels are all-or-nothing (docs/evaluation.md); LLM gates come from the pack.
    "evals": {
        "judge_profile": "judge",
        "suites": {
            "deterministic": {"gate": {"pass_rate": {"min": 1.0}}},
            "flows": {"gate": {"pass_rate": {"min": 1.0}}},
        },
    },
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


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError([f"{p}: invalid JSON ({exc.msg} at line {exc.lineno})"]) from exc


def load_layer(directory: Path) -> dict[str, Any]:
    """Load every *.json file of a config directory into {stem: content}, plus ``flows/*.json`` into
    ``{"flows": {stem: flow}}``. A missing directory is empty."""
    if not directory or not directory.is_dir():
        return {}
    out: dict[str, Any] = {}
    for p in sorted(directory.glob("*.json")):
        content = _read_json(p)
        _check_layer_shape(p, p.stem, content)
        out[p.stem] = content
    flows_dir = directory / "flows"
    if flows_dir.is_dir():
        flows: dict[str, Any] = {}
        for p in sorted(flows_dir.glob("*.json")):
            content = _read_json(p)
            if not isinstance(content, dict):
                raise ConfigError([f"{p}: expected a JSON object"])
            flows[p.stem] = content
        if flows:
            out["flows"] = flows
    return out


def load_assets(*directories: Path | None) -> dict[str, str]:
    """Text assets (prompts, instructions, contexts, schemas) of the layers; later layers win per path."""
    from pkpipe.engine.snapshot import ASSET_DIRS, ASSET_SUFFIXES

    assets: dict[str, str] = {}
    for base in directories:
        if not base or not base.is_dir():
            continue
        root = base.resolve()
        for sub in ASSET_DIRS:
            folder = base / sub
            if not folder.is_dir():
                continue
            for p in sorted(folder.rglob("*")):
                if p.is_file() and p.suffix in ASSET_SUFFIXES and p.resolve().is_relative_to(root):
                    assets[p.relative_to(base).as_posix()] = p.read_text(encoding="utf-8")
    return assets


def snapshot_version(data: Mapping[str, Any], assets: Mapping[str, str]) -> str:
    """Version of config + assets; equals :func:`config_version` of the data when there are no assets."""
    return config_version({"config": data, "assets": dict(assets)}) if assets else config_version(data)


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
    assets: dict[str, str] = field(default_factory=dict)

    def snapshot(self):
        """The pinned, in-memory form a run executes against (spec 005)."""
        from pkpipe.engine.snapshot import ConfigSnapshot

        return ConfigSnapshot(self.version, copy.deepcopy(self.data), dict(self.assets))

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
    from pkpipe.domain.session_plan import plan_errors

    for cat in cfg.data.get("categories", []):
        e, w = plan_errors(
            cat,
            servers=servers,
            asset=lambda rel: (lambda f: f.read_text(encoding="utf-8") if f else None)(cfg.find_file(rel)),
        )
        errors += e
        warnings += w
    errors += _check_judge_profile(cfg.data)
    deny = cfg.data.get("security", {}).get("url_filter", {}).get("deny_list")
    if deny and cfg.find_file(deny) is None:
        warnings.append(f"security.url_filter.deny_list '{deny}' not found")
    from pkpipe.engine.compiler import validate_config_flows

    flow_errors, flow_warnings = validate_config_flows(cfg.snapshot())
    errors += flow_errors
    warnings += flow_warnings
    return errors, warnings


def _check_judge_profile(data: dict[str, Any]) -> list[str]:
    """Spec 013 FR-002: the judge profile exists and is never a profile that an LLM step runs on."""
    judge = (data.get("evals") or {}).get("judge_profile")
    profiles = data.get("models") or {}
    if not judge or not profiles:
        return []
    if judge not in profiles:
        return [f"evals.json: judge_profile '{judge}' is not a models.json profile"]
    used = set()
    for flow in (data.get("flows") or {}).values():
        for node in flow.get("nodes", []):
            if node.get("type") in ("llm", "llm_tools"):
                used.add((node.get("config") or {}).get("profile", "default"))
    if judge in used:
        return [f"evals.json: judge_profile '{judge}' is also used by an LLM step; the judge must differ"]
    return []


VAULT_POLICY_ENV = "PKPIPE_VAULT_POLICY"


def vault_policy_errors(data: dict[str, Any], policy_file: Path) -> list[str]:
    """The effective ``layout`` against the vault policy file the vault server runs with (spec 020 FR-008)."""
    from pkpipe.domain.layout import layout_from, policy_file_errors

    try:
        policy = json.loads(policy_file.read_text(encoding="utf-8"))
        layout = layout_from(data.get("layout"))
    except (OSError, ValueError) as exc:
        return [f"vault policy '{policy_file}': {exc}"]
    return [f"{policy_file.name}: {e}" for e in policy_file_errors(layout, policy)]


def load_effective(
    pack_dir: Path | None = None,
    overlay_dir: Path | None = None,
    *,
    strict: bool = True,
    vault_policy: Path | None = None,
) -> EffectiveConfig:
    """Load pack + optional overlay, validate shape and references, compute the version hash.

    ``overlay_dir=None`` falls back to ``PKPIPE_OVERLAY_DIR``. Raises :class:`ConfigError` when ``strict``.
    A ``vault_policy`` file (or ``PKPIPE_VAULT_POLICY``) is checked against the layout, so a publish or
    ``pkpipe validate`` fails when the overlay layout and the vault policy disagree (spec 020 FR-008).
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
    assets = load_assets(pack_dir, overlay_dir)
    cfg = EffectiveConfig(data, snapshot_version(data, assets), pack_dir, overlay_dir, warnings, assets)
    errors = validate_effective(data)
    policy_file = vault_policy or (
        Path(os.environ[VAULT_POLICY_ENV]) if os.environ.get(VAULT_POLICY_ENV) else None
    )
    if policy_file is not None and not errors:
        errors += vault_policy_errors(data, policy_file)
    ref_errors, ref_warnings = check_references(cfg) if not errors else ([], [])
    cfg.warnings.extend(ref_warnings)
    errors += ref_errors
    if errors and strict:
        raise ConfigError(errors)
    return cfg
