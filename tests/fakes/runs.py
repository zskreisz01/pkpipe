from __future__ import annotations

import copy
import threading
from typing import Any


class InMemoryRunStore:
    """RunStorePort fake: run records and config snapshots in dicts (copies in and out)."""

    UPDATABLE = frozenset({"status", "ended_at", "error"})

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.snapshots: dict[str, tuple[dict[str, Any], dict[str, str]]] = {}
        self.status_log: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def create_run(self, record: dict[str, Any]) -> None:
        with self._lock:
            if record["run_id"] in self.runs:
                raise ValueError(f"run '{record['run_id']}' exists")
            self.runs[record["run_id"]] = copy.deepcopy(record)
            self.status_log.append((record["run_id"], record["status"]))

    def update_run(self, run_id: str, **fields: Any) -> None:
        bad = set(fields) - self.UPDATABLE
        if bad:
            raise ValueError(f"run fields {sorted(bad)} cannot be changed")
        with self._lock:
            if run_id not in self.runs:
                raise KeyError(run_id)
            self.runs[run_id].update(copy.deepcopy(fields))
            if "status" in fields:
                self.status_log.append((run_id, fields["status"]))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self.runs.get(run_id)
            return copy.deepcopy(r) if r else None

    def list_runs(self, *, status: str | None = None, flow_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [
                copy.deepcopy(r)
                for r in self.runs.values()
                if (status is None or r["status"] == status) and (flow_id is None or r["flow_id"] == flow_id)
            ]

    def save_snapshot(self, version: str, data: dict[str, Any], assets: dict[str, str]) -> None:
        with self._lock:
            self.snapshots.setdefault(version, (copy.deepcopy(data), dict(assets)))

    def load_snapshot(self, version: str) -> tuple[dict[str, Any], dict[str, str]] | None:
        with self._lock:
            s = self.snapshots.get(version)
            return (copy.deepcopy(s[0]), dict(s[1])) if s else None
