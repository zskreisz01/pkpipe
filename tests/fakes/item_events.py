"""In-memory ItemEventPort (spec 030)."""

from __future__ import annotations

from typing import Any


class FakeItemEvents:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def record(self, item_id, kind, *, run_id=None, flow_id=None, step=None, outcome=None, detail=None):
        self.rows.append(
            {
                "at": f"2026-01-01T00:00:{len(self.rows):02d}",
                "item_id": item_id,
                "kind": kind,
                "run_id": run_id,
                "flow_id": flow_id,
                "step": step,
                "outcome": outcome,
                "detail": dict(detail or {}),
            }
        )

    def events_of(self, item_id, limit=500):
        return [r for r in self.rows if r["item_id"] == item_id][:limit]

    def recent_item_ids(self, limit=50):
        seen: list[str] = []
        for r in reversed(self.rows):
            if r["item_id"] not in seen:
                seen.append(r["item_id"])
        return seen[:limit]
