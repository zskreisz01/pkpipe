"""Restore a run (spec 019 US2): the one rule set behind the CLI, the UI and the app MCP tool.

The restore itself is the snapshot port's job; what lives here is what the port cannot know: a run that is
still active is never restored (its post manifest does not exist yet and it keeps writing), and a short run id
prefix resolves to the one snapshot it names.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pkpipe.domain.vault_snapshot import RestorePlan, SnapshotError, SnapshotInfo, UnknownSnapshot
from pkpipe.engine.runs import ACTIVE, Run
from pkpipe.ports import SnapshotPort

MIN_PREFIX = 6


class RunLookup(Protocol):
    """The run table, read only (``RunService`` and the CLI's table reader both fit)."""

    def get(self, run_id: str) -> Run | None: ...


class RestoreRefused(SnapshotError):
    """The restore is not allowed now (an active run); the message says why."""


def resolve_run_id(infos: Sequence[SnapshotInfo], given: str) -> str:
    """``given`` as a snapshot run id: exact, or the unique prefix (at least 6 characters) of one."""
    ids = [i.run_id for i in infos if i.status != "pruned"]
    if given in ids:
        return given
    matches = [i for i in ids if len(given) >= MIN_PREFIX and i.startswith(given)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SnapshotError(f"'{given}' names {len(matches)} snapshots; give more of the run id")
    if len(given) < MIN_PREFIX and given not in ids:
        raise UnknownSnapshot(
            f"no snapshot of run '{given}' (a run id prefix needs at least {MIN_PREFIX} characters)"
        )
    return given  # unknown: the port reports it (never taken, a read-only flow, or pruned)


def restore_run(
    snapshots: SnapshotPort,
    run_id: str,
    *,
    apply: bool = False,
    force: bool = False,
    runs: RunLookup | None = None,
) -> RestorePlan:
    """The plan of restoring ``run_id`` (a dry run), or with ``apply`` the applied plan."""
    run_id = resolve_run_id(snapshots.list(), run_id)
    if runs is not None:
        run = runs.get(run_id)
        if run is not None and run.status in ACTIVE:
            raise RestoreRefused(f"run '{run_id}' is {run.status.value}; restore it after it ended")
    if apply:
        return snapshots.apply_restore(run_id, force=force)
    return snapshots.plan_restore(run_id, force=force)


#: What a restore never touches (spec 019 clarification 3); printed with every applied restore.
NOT_COVERED = (
    "not restored: the app DB, the learning gateway's job store, Open Notebook content and messages "
    "already sent"
)
