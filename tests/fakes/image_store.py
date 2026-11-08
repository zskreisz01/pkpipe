from __future__ import annotations

import copy
from typing import Any


class FakeImageStore:
    """ImageStorePort fake: the description cache and the timing table in memory (spec 021)."""

    def __init__(self, timings: dict[str, list[float]] | None = None):
        self.cache: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.timings: list[dict[str, Any]] = []
        self._seed = {m: list(v) for m, v in (timings or {}).items()}  # newest first

    def cache_get(self, sha256: str, model: str, prompt_version: str) -> dict[str, Any] | None:
        hit = self.cache.get((sha256, model, prompt_version))
        return copy.deepcopy(hit) if hit is not None else None

    def cache_get_latest(self, sha256: str, model: str) -> dict[str, Any] | None:
        hits = [v for (sh, m, _), v in self.cache.items() if sh == sha256 and m == model]
        return copy.deepcopy(hits[-1]) if hits else None

    def cache_put(self, sha256: str, model: str, prompt_version: str, description: dict[str, Any]) -> None:
        self.cache[(sha256, model, prompt_version)] = copy.deepcopy(description)

    def record_timing(self, run_id: str, sha256: str, model: str, seconds: float) -> None:
        self.timings.append({"run_id": run_id, "sha256": sha256, "model": model, "seconds": seconds})

    def recent_seconds(self, model: str, limit: int = 50) -> list[float]:
        mine = [t["seconds"] for t in reversed(self.timings) if t["model"] == model]
        return (mine + self._seed.get(model, []))[:limit]
