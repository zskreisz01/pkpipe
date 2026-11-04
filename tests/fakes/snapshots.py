from __future__ import annotations

from typing import Any

from pkpipe.domain.vault_snapshot import RestorePlan, SnapshotInfo


class FakeSnapshots:
    """SnapshotPort fake: records the calls; ``fail`` makes ``take`` raise (a full disk, a bad permission)."""

    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.calls: list[tuple[str, str]] = []
        self.taken: dict[str, str] = {}

    def take(self, run_id: str, flow_id: str) -> SnapshotInfo:
        self.calls.append(("take", run_id))
        if self.fail:
            raise self.fail
        self.taken[run_id] = flow_id
        return SnapshotInfo(
            run_id=run_id, flow_id=flow_id, taken_at="2026-11-04T00:00:00+00:00", files=0, bytes=0
        )

    def finish(self, run_id: str) -> SnapshotInfo | None:
        self.calls.append(("finish", run_id))
        if run_id not in self.taken:
            return None
        return SnapshotInfo(
            run_id=run_id,
            flow_id=self.taken[run_id],
            taken_at="2026-11-04T00:00:00+00:00",
            files=0,
            bytes=0,
            finished=True,
        )

    def plan_restore(self, run_id: str, *, force: bool = False) -> RestorePlan:
        self.calls.append(("plan", run_id))
        return RestorePlan(run_id=run_id)

    def apply_restore(self, run_id: str, *, force: bool = False) -> RestorePlan:
        self.calls.append(("apply", run_id))
        return RestorePlan(run_id=run_id, applied=True)

    def list(self) -> list[SnapshotInfo]:
        return []

    def show(self, run_id: str) -> dict[str, Any]:
        return {}

    def prune(self) -> dict[str, Any]:
        return {"pruned": []}

    def summary(self) -> dict[str, Any]:
        return {}
