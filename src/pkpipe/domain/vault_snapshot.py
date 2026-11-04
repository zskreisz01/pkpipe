"""Vault snapshots (spec 019): the formats and the rules, no file system.

A snapshot is the state of the vault files a run can change, taken before the run (``Manifest``); the end of
the run records what it left different (``PostManifest``). A restore compares those two with the current state
and plans four verbs (``restore``, ``remove``, ``move_back``, ``skip``). Code decides every step
(principle V); :func:`diff_manifests` and :func:`build_plan` are pure functions over hashes, so the rules
are unit-tested without a vault.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MANIFEST_VERSION = 1
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
#: A folder in place of a file, or a symlink: never equal to a real sha256, so it always reads as "changed".
NOT_A_FILE = "<not-a-file>"

#: Vault tools that change the vault (FR-001). A flow that can call one takes a snapshot.
WRITING_VAULT_TOOLS = frozenset(
    {
        "write_note",
        "append_note",
        "move_note",
        "update_triage",
        "upsert_processing_list",
        "update_improvements",
        "update_tool_candidates",
        "quarantine_note",
        "inbox_remove_entries",
    }
)
#: Vault tools that only read. Together with the writing tools this is every tool of the vault policy; a test
#: fails when a new vault tool is in neither set (plan: risks).
READ_ONLY_VAULT_TOOLS = frozenset(
    {
        "list_folder",
        "read_note",
        "read_triage",
        "read_notes",
        "read_improvements",
        "read_tool_candidates",
    }
)

Verb = Literal["restore", "remove", "move_back", "skip"]
PostState = Literal["modified", "created", "removed", "moved_from"]
Status = Literal["kept", "restored", "pruned"]


class SnapshotError(Exception):
    """A snapshot or restore cannot be done (the message names the cause, never a note path or text)."""


class UnknownSnapshot(SnapshotError, LookupError):
    """No snapshot of that run (never taken, a read-only flow, or pruned)."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FileEntry(_Model):
    path: str
    size: int
    sha256: str
    mtime: int
    #: Bytes copied into ``files/``; False for a conflict copy or a file over ``max_file_bytes``.
    copied: bool = True
    #: A sync-conflict copy (spec 004): listed, never restored over or removed.
    conflict: bool = False


class Manifest(_Model):
    version: int = MANIFEST_VERSION
    run_id: str
    flow_id: str
    taken_at: str
    #: sha256 of the effective write scope (folders, excluded folders, managed files).
    policy_digest: str
    scope: list[str]
    managed: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    files: list[FileEntry] = Field(default_factory=list)


class PostEntry(_Model):
    path: str
    state: PostState
    size: int | None = None
    sha256: str | None = None
    mtime: int | None = None
    #: ``moved_from`` only: where the note was before the run.
    from_path: str | None = Field(default=None, alias="from")


class PostManifest(_Model):
    version: int = MANIFEST_VERSION
    run_id: str
    finished_at: str
    entries: list[PostEntry] = Field(default_factory=list)


class RestoreStep(_Model):
    verb: Verb
    path: str | None = None
    from_path: str | None = Field(default=None, alias="from")
    to: str | None = None
    reason: str | None = None
    #: Save the bytes now at the target in ``trash/`` before the step overwrites them (``--force``).
    save_current: bool = False
    #: Set when the plan is applied: ``done``, or ``skipped:<reason>`` when the file changed meanwhile.
    outcome: str | None = None


class RestorePlan(_Model):
    run_id: str
    applied: bool = False
    steps: list[RestoreStep] = Field(default_factory=list)
    #: The snapshot of the state before the restore was applied (a restore is undoable too).
    safety_snapshot: str | None = None

    def counts(self) -> dict[str, int]:
        out = {v: 0 for v in ("restore", "remove", "move_back", "skip")}
        for step in self.steps:
            out[step.verb] += 1
        return out

    @property
    def conflicts(self) -> list[RestoreStep]:
        return [s for s in self.steps if s.verb == "skip"]

    @property
    def actionable(self) -> list[RestoreStep]:
        return [s for s in self.steps if s.verb != "skip"]

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_defaults=False)


class SnapshotInfo(_Model):
    """One row of ``pkpipe snapshot list``."""

    run_id: str
    flow_id: str
    taken_at: str
    files: int
    bytes: int
    status: Status = "kept"
    finished: bool = False
    reverted: bool = False
    restored_at: str | None = None


# -- run ids -------------------------------------------------------------------------------------------------
def check_run_id(run_id: str) -> str:
    """The run id as a directory name; anything that could leave the snapshot folder is refused."""
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id) or ".." in run_id:
        raise SnapshotError("run id must be 1-128 letters, digits, '.', '_' or '-'")
    return run_id


# -- which flows snapshot ------------------------------------------------------------------------------------
_TOOL_NAME = re.compile(r"[A-Za-z0-9_.-]+:([A-Za-z0-9_]+)")


def tool_names(value: Any) -> Iterable[str]:
    """Every ``<server>:<tool>`` string anywhere in a node config (``tool``, ``tools``, ``calls[].tool``),
    as the tool part only: the server key is a local name, the tool name is what the vault policy knows."""
    if isinstance(value, str):
        if m := _TOOL_NAME.fullmatch(value):
            yield m.group(1)
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from tool_names(v)
    elif isinstance(value, Sequence):
        for v in value:
            yield from tool_names(v)


def flow_writes_vault(flows: Mapping[str, Mapping[str, Any]], flow_id: str) -> bool:
    """Whether ``flow_id`` (or a subflow it calls) can call a writing vault tool (FR-001). Reads the flow
    definitions as data. A subflow chosen at run time (``${item.subflow}``) counts as writing: a snapshot
    that was not needed costs a few files, a missing one costs the way back."""
    seen: set[str] = set()

    def walk(fid: str) -> bool:
        if fid in seen:
            return False
        seen.add(fid)
        for node in (flows.get(fid) or {}).get("nodes", []):
            config = node.get("config") or {}
            if any(t in WRITING_VAULT_TOOLS for t in tool_names(config)):
                return True
            if node.get("type") == "subflow":
                target = config.get("flow")
                if not isinstance(target, str) or "${" in target:
                    return True
                if walk(target):
                    return True
        return False

    return walk(flow_id)


# -- scope ---------------------------------------------------------------------------------------------------
def reduce_folders(folders: Iterable[str]) -> list[str]:
    """Sorted folders without any that lies below another one of them (``2-Areas/x`` below ``2-Areas``)."""
    parts = sorted({tuple(f.strip("/").split("/")) for f in folders if f.strip("/")})
    kept: list[tuple[str, ...]] = []
    for p in parts:
        if not any(p[: len(k)] == k for k in kept):
            kept.append(p)
    return ["/".join(k) for k in kept]


def under(path: str, folder: str) -> bool:
    """Whether ``path`` is ``folder`` or lies below it (segment wise)."""
    p, f = PurePosixPath(path).parts, PurePosixPath(folder).parts
    return p[: len(f)] == f


# -- post manifest -------------------------------------------------------------------------------------------
def diff_manifests(pre: Mapping[str, FileEntry], now: Mapping[str, FileEntry]) -> list[PostEntry]:
    """What the run left different from ``pre``: ``modified``, ``created``, ``removed`` and ``moved_from``
    (a created file with the bytes of a removed one; matched one to one, same file name first)."""
    entries: dict[str, PostEntry] = {}
    removed = {p: pre[p] for p in pre if p not in now}
    created = {p: now[p] for p in now if p not in pre}
    for path, entry in now.items():
        old = pre.get(path)
        if old is not None and old.sha256 != entry.sha256:
            entries[path] = PostEntry(
                path=path, state="modified", size=entry.size, sha256=entry.sha256, mtime=entry.mtime
            )
    by_sha: dict[str, list[str]] = {}
    for path in sorted(removed):
        by_sha.setdefault(removed[path].sha256, []).append(path)
    for path in sorted(created):
        candidates = by_sha.get(created[path].sha256) or []
        if candidates:
            same_name = [c for c in candidates if PurePosixPath(c).name == PurePosixPath(path).name]
            source = (same_name or candidates)[0]
            candidates.remove(source)
            removed.pop(source)
            entry = created[path]
            entries[path] = PostEntry(
                path=path,
                state="moved_from",
                size=entry.size,
                sha256=entry.sha256,
                mtime=entry.mtime,
                from_path=source,
            )
        else:
            entry = created[path]
            entries[path] = PostEntry(
                path=path, state="created", size=entry.size, sha256=entry.sha256, mtime=entry.mtime
            )
    for path in removed:
        entries[path] = PostEntry(path=path, state="removed")
    return [entries[p] for p in sorted(entries)]


# -- restore plan --------------------------------------------------------------------------------------------
_ORDER = {"move_back": 0, "restore": 1, "remove": 2, "skip": 3}


def build_plan(
    pre: Mapping[str, FileEntry],
    post: Sequence[PostEntry],
    current: Callable[[str], str | None],
    *,
    force: bool = False,
    run_id: str = "",
) -> RestorePlan:
    """The steps that bring the paths of ``post`` back to ``pre``.

    ``current(path)`` is the sha256 of the file now at ``path`` (``None`` = absent, ``NOT_A_FILE`` for a
    folder or symlink). A path whose current bytes are neither the pre-run bytes (already restored: nothing to
    do) nor the bytes the run left (``post``) was changed since the run: ``skip``, or with ``force`` the step
    that saves the current bytes in the trash first. Paths the run did not touch never appear: an owner edit
    to them is out of the plan by construction (SC-004). Applying a restore twice plans nothing the second
    time (SC-003)."""
    steps: list[RestoreStep] = []

    def skip(path: str, reason: str) -> None:
        steps.append(RestoreStep(verb="skip", path=path, reason=reason))

    def conflicts(entry: FileEntry | None) -> str | None:
        if entry is None:
            return None
        if entry.conflict:
            return "sync_conflict_copy"
        if not entry.copied:
            return "not_copied"
        return None

    for e in post:
        cur = current(e.path)
        if e.state in ("modified", "removed"):
            old = pre.get(e.path)
            if old is None:
                skip(e.path, "not_in_snapshot")
            elif old.conflict:
                skip(e.path, "sync_conflict_copy")
            elif cur == old.sha256:
                continue  # already restored
            elif reason := conflicts(old):
                skip(e.path, reason)
            elif e.state == "removed" and cur is None:
                steps.append(RestoreStep(verb="restore", path=e.path))
            elif e.state == "modified" and cur == e.sha256:
                steps.append(RestoreStep(verb="restore", path=e.path))
            elif cur is None:
                if force:
                    steps.append(RestoreStep(verb="restore", path=e.path, reason="forced: deleted_since_run"))
                else:
                    skip(e.path, "deleted_since_run")
            elif force:
                steps.append(
                    RestoreStep(
                        verb="restore", path=e.path, reason="forced: changed_since_run", save_current=True
                    )
                )
            else:
                skip(e.path, "changed_since_run")
        elif e.state == "created":
            if cur is None:
                continue  # already gone
            if cur == e.sha256:
                steps.append(RestoreStep(verb="remove", path=e.path))
            elif force:
                steps.append(RestoreStep(verb="remove", path=e.path, reason="forced: changed_since_run"))
            else:
                skip(e.path, "changed_since_run")
        elif e.state == "moved_from" and e.from_path:
            src, dst = e.from_path, e.path
            src_now = current(src)
            origin = pre.get(src)
            if cur is None:
                if origin is not None and src_now == origin.sha256:
                    continue  # already back
                skip(dst, "moved_note_missing")
            elif cur != e.sha256:
                if force and origin is not None and not conflicts(origin):
                    steps.append(
                        RestoreStep(
                            verb="restore",
                            path=src,
                            reason="forced: changed_since_run",
                            save_current=src_now is not None,
                        )
                    )
                    steps.append(RestoreStep(verb="remove", path=dst, reason="forced: changed_since_run"))
                else:
                    skip(dst, "changed_since_run")
            elif src_now is not None:
                if force and origin is not None and not conflicts(origin):
                    steps.append(
                        RestoreStep(
                            verb="restore", path=src, reason="forced: destination_exists", save_current=True
                        )
                    )
                    steps.append(RestoreStep(verb="remove", path=dst, reason="forced: destination_exists"))
                else:
                    skip(dst, "destination_exists")
            else:
                steps.append(RestoreStep(verb="move_back", from_path=dst, to=src))
    steps.sort(key=lambda s: (_ORDER[s.verb], s.path or s.from_path or ""))
    return RestorePlan(run_id=run_id, steps=steps)


__all__ = [
    "MANIFEST_VERSION",
    "NOT_A_FILE",
    "READ_ONLY_VAULT_TOOLS",
    "WRITING_VAULT_TOOLS",
    "FileEntry",
    "Manifest",
    "PostEntry",
    "PostManifest",
    "RestorePlan",
    "RestoreStep",
    "SnapshotError",
    "SnapshotInfo",
    "UnknownSnapshot",
    "build_plan",
    "check_run_id",
    "diff_manifests",
    "flow_writes_vault",
    "reduce_folders",
    "tool_names",
    "under",
]
