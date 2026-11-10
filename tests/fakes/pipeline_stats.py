"""In-memory PipelineStatsPort (spec 033)."""

from __future__ import annotations

from typing import Any


class FakePipelineStats:
    def __init__(
        self,
        *,
        retries: dict[str, int] | None = None,
        held: dict[str, dict[str, Any]] | None = None,
        events: dict[str, set[str]] | None = None,
        last: dict[str, str] | None = None,
        runs: dict[str, dict[str, int]] | None = None,
    ) -> None:
        self.retries = dict(retries or {})
        self._held = dict(held or {})
        self.events = {k: set(v) for k, v in (events or {}).items()}
        self.last = dict(last or {})
        self.runs = dict(runs or {})

    def retry_counts(self) -> dict[str, int]:
        return dict(self.retries)

    def held(self) -> dict[str, dict[str, Any]]:
        return dict(self._held)

    def item_ids_with_event(self, kind: str) -> set[str]:
        return set(self.events.get(kind, set()))

    def last_event_times(self) -> dict[str, str]:
        return dict(self.last)

    def run_item_counts(self, run_ids: list[str]) -> dict[str, dict[str, int]]:
        return {r: self.runs.get(r, {"items": 0, "failed_steps": 0}) for r in run_ids}
