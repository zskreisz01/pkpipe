from __future__ import annotations

from typing import Any

from pkpipe.engine.registry import config_version, merge_layers


class InMemoryConfig:
    """ConfigPort fake built from already-loaded layers (no files)."""

    def __init__(self, *layers: dict[str, Any]):
        self._layers = [dict(layer) for layer in layers]

    def load_layers(self) -> list[dict[str, Any]]:
        return [dict(layer) for layer in self._layers]

    def version(self) -> str:
        return config_version(merge_layers(*self._layers))
