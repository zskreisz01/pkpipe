"""Pinned, in-memory config of one run (spec 005 US2, T007).

A run compiles and executes against a :class:`ConfigSnapshot`: the merged JSON *and* the text assets (system
prompts, instructions, context packs, schemas). The run service stores the snapshot under its version, so a
config publish while a run waits, or a restart, never changes what the run sees.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from pkpipe.engine.flow_models import Flow, FlowsConfig

ASSET_DIRS = ("prompts", "instructions", "contexts", "schemas")
ASSET_SUFFIXES = (".md", ".json", ".txt")


def safe_asset_path(relative: str) -> str | None:
    """Normalised relative path, or None for absolute paths, ``..``, backslashes and empty parts."""
    if not relative or "\\" in relative or relative.startswith("/"):
        return None
    parts = PurePosixPath(relative).parts
    if any(p in ("", ".", "..") for p in parts) or ":" in parts[0]:
        return None
    return "/".join(parts)


@dataclass(frozen=True)
class ConfigSnapshot:
    version: str
    data: dict[str, Any]
    assets: dict[str, str] = field(default_factory=dict)

    def asset(self, relative: str) -> str | None:
        rel = safe_asset_path(relative)
        return self.assets.get(rel) if rel else None

    def json_asset(self, relative: str) -> Any:
        text = self.asset(relative)
        return None if text is None else json.loads(text)

    def flows(self) -> dict[str, Flow]:
        return FlowsConfig.model_validate(self.data.get("flows", {})).root

    def flow(self, flow_id: str) -> Flow:
        flows = self.flows()
        if flow_id not in flows:
            raise KeyError(f"unknown flow '{flow_id}' (known: {', '.join(sorted(flows)) or 'none'})")
        return flows[flow_id]

    def section(self, name: str, default: Any = None) -> Any:
        """A deep copy of one config file's content, so steps cannot mutate the pinned config."""
        return copy.deepcopy(self.data.get(name, default))
