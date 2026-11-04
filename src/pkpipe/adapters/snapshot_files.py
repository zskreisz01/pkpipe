"""File-system adapter of the ``SnapshotPort`` (spec 019).

``$PKPIPE_DATA_DIR/snapshots/<run id>/`` holds ``manifest.json`` (before the run), ``post.json`` (what the run
left behind), ``files/<path>`` (the bytes before the run) and ``trash/<path>`` (files a restore moved out of
the vault). The directory is mode 0700 and never inside the vault, the overlay or a repository. Plain files
and the standard library only: it works without git and cannot interfere with the owner's commits.

The snapshot is taken from the scope the vault policy derives (:mod:`pkpipe.obsidian.scope`); a restore needs
no policy, only the scope recorded in the manifest. Spans carry the run id and counts, never paths or text.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import secrets
import shutil
import tempfile
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from pkpipe.domain.vault_snapshot import (
    NOT_A_FILE,
    FileEntry,
    Manifest,
    PostManifest,
    RestorePlan,
    RestoreStep,
    SnapshotError,
    SnapshotInfo,
    UnknownSnapshot,
    build_plan,
    check_run_id,
    diff_manifests,
    under,
)
from pkpipe.obsidian.atomic import dir_lock
from pkpipe.obsidian.policy import VaultPolicy, is_conflict_name
from pkpipe.obsidian.scope import SnapshotScope, derive_scope
from pkpipe.telemetry import tool_span

MANIFEST, POST, FILES, TRASH = "manifest.json", "post.json", "files", "trash"
DEFAULT_RETENTION_DAYS = 14
DEFAULT_MAX_BYTES = 1024**3
DEFAULT_MAX_FILE_BYTES = 5 * 1024**2
_CHUNK = 1024 * 1024


class SnapshotIndex(Protocol):
    """The ``snapshots`` table of the app DB (implemented by ``AppStore``)."""

    def snapshot_upsert(self, row: dict[str, Any]) -> None: ...

    def snapshot_get(self, run_id: str) -> dict[str, Any] | None: ...

    def snapshot_list(self) -> list[dict[str, Any]]: ...

    def snapshot_update(self, run_id: str, **fields: Any) -> None: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _cause(exc: OSError) -> str:
    """The reason of an OS error without its file name (names can be private notes)."""
    return exc.strerror or errno.errorcode.get(exc.errno or 0, type(exc).__name__)


def _valid_rel(rel: str) -> bool:
    """A vault relative note path the policy would accept (no hidden, empty or ``..`` segments)."""
    if not rel or "\x00" in rel or "\\" in rel or ":" in rel or rel.startswith(("/", "~")):
        return False
    parts = rel.split("/")
    return all(p not in ("", ".", "..") and not p.startswith(".") for p in parts) and rel.endswith(".md")


def _hash_file(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _write_json(path: Path, model: Any) -> None:
    text = json.dumps(model.model_dump(mode="json", by_alias=True, exclude_none=True), indent=1) + "\n"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


class FileSnapshots:
    def __init__(
        self,
        directory: Path,
        vault_dir: Path,
        *,
        index: SnapshotIndex,
        policy: VaultPolicy | None = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        now: Callable[[], datetime] = _now,
    ):
        self.directory = Path(directory)
        self.vault_dir = Path(vault_dir)
        self.policy = policy
        self.index = index
        self.retention_days = retention_days
        self.max_bytes = max_bytes
        self.max_file_bytes = max_file_bytes
        self._now = now

    # -- paths -------------------------------------------------------------------------------------------
    @property
    def root(self) -> Path:
        return self.vault_dir.resolve()

    def _ensure_dir(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)

    def _snapshot_dir(self, run_id: str) -> Path:
        return self.directory / check_run_id(run_id)

    def _vault_path(self, rel: str) -> Path:
        """The absolute path of a manifest path, refused when it could leave the vault or pass a symlink."""
        if not _valid_rel(rel):
            raise SnapshotError("a path in the snapshot is not a valid vault note path")
        root = self.root
        current = root
        for part in rel.split("/")[:-1]:
            current = current / part
            if current.is_symlink():
                raise SnapshotError("a folder in the snapshot's paths is a symlink now")
        return root / rel

    def _load(self, run_id: str) -> tuple[Path, Manifest]:
        base = self._snapshot_dir(run_id)
        try:
            manifest = Manifest.model_validate_json((base / MANIFEST).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise UnknownSnapshot(f"no snapshot of run '{run_id}'") from None
        except (ValueError, OSError) as exc:
            raise SnapshotError(f"the manifest of run '{run_id}' is unreadable") from exc
        return base, manifest

    def _load_post(self, base: Path, run_id: str) -> PostManifest | None:
        try:
            return PostManifest.model_validate_json((base / POST).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (ValueError, OSError) as exc:
            raise SnapshotError(f"the post manifest of run '{run_id}' is unreadable") from exc

    # -- scanning ----------------------------------------------------------------------------------------
    def _walk(self, scope: SnapshotScope) -> Iterator[str]:
        """Relative paths of the ``.md`` files in scope: no symlinks, no hidden names, nothing excluded."""
        root = self.root
        seen: set[str] = set()
        for folder in scope.folders:
            top = root / folder
            if top.is_symlink() or not top.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
                here = Path(dirpath).relative_to(root).as_posix()
                dirnames[:] = sorted(
                    d
                    for d in dirnames
                    if not d.startswith(".")
                    and not (Path(dirpath) / d).is_symlink()
                    and not any(under(f"{here}/{d}", ex) for ex in scope.exclude)
                )
                for name in sorted(filenames):
                    rel = f"{here}/{name}"
                    if rel not in seen and _valid_rel(rel) and not (Path(dirpath) / name).is_symlink():
                        seen.add(rel)
                        yield rel
        for rel in scope.managed:
            if rel not in seen and _valid_rel(rel) and not any(under(rel, ex) for ex in scope.exclude):
                seen.add(rel)
                yield rel

    def _scan(self, scope: SnapshotScope, copy_into: Path | None = None) -> list[FileEntry]:
        """Manifest entries of every file in scope; with ``copy_into`` the bytes are copied there and the
        recorded hash is the hash of the copied bytes (an Obsidian autosave cannot make the manifest claim a
        state it did not copy)."""
        entries: list[FileEntry] = []
        root = self.root
        for rel in self._walk(scope):
            path = root / rel
            try:
                st = path.lstat()
                if not path.is_file() or path.is_symlink():
                    continue
                conflict = is_conflict_name(path.name)
                copy_it = copy_into is not None and not conflict and st.st_size <= self.max_file_bytes
                if copy_it or st.st_size <= self.max_file_bytes:
                    data = path.read_bytes()
                    sha, size = hashlib.sha256(data).hexdigest(), len(data)
                else:
                    data = None
                    sha, size = _hash_file(path)
                if copy_it and data is not None and copy_into is not None:
                    target = copy_into / rel
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    target.write_bytes(data)
                    os.chmod(target, 0o600)
            except FileNotFoundError:
                continue  # vanished between listing and reading
            entries.append(
                FileEntry(
                    path=rel,
                    size=size,
                    sha256=sha,
                    mtime=int(st.st_mtime),
                    copied=copy_it,
                    conflict=conflict,
                )
            )
        return entries

    def _current_sha(self, rel: str) -> str | None:
        try:
            path = self._vault_path(rel)
        except SnapshotError:
            return NOT_A_FILE  # a symlinked folder on the way: never read or written through it
        try:
            path.lstat()
        except FileNotFoundError:
            return None
        if path.is_symlink() or not path.is_file():
            return NOT_A_FILE
        try:
            return _hash_file(path)[0]
        except FileNotFoundError:
            return None

    # -- take / finish -----------------------------------------------------------------------------------
    def take(self, run_id: str, flow_id: str) -> SnapshotInfo:
        """Snapshot the scope the vault policy derives, before the first write of the run (FR-001, FR-002)."""
        if self.policy is None:
            raise SnapshotError("no vault policy: cannot derive the snapshot scope")
        return self._take(run_id, flow_id, derive_scope(self.policy))

    def _take(self, run_id: str, flow_id: str, scope: SnapshotScope) -> SnapshotInfo:
        final = self._snapshot_dir(run_id)
        with tool_span("snapshot.take") as span:
            span.set_attribute("tool.server", "pkpipe-snapshots")
            span.set_attribute("pkpipe.snapshot.run_id", run_id)
            if not self.root.is_dir():
                raise SnapshotError("the vault folder does not exist")
            self._ensure_dir()
            if final.exists():
                raise SnapshotError(f"a snapshot of run '{run_id}' exists already")
            tmp = Path(tempfile.mkdtemp(dir=self.directory, prefix=f".tmp-{run_id[:40]}-"))
            try:
                os.chmod(tmp, 0o700)
                (tmp / FILES).mkdir(mode=0o700)
                entries = self._scan(scope, tmp / FILES)
                manifest = Manifest(
                    run_id=run_id,
                    flow_id=flow_id,
                    taken_at=self._now().isoformat(timespec="seconds"),
                    policy_digest=scope.digest,
                    scope=list(scope.folders),
                    managed=list(scope.managed),
                    exclude=list(scope.exclude),
                    files=entries,
                )
                _write_json(tmp / MANIFEST, manifest)
                os.replace(tmp, final)
            except OSError as exc:
                shutil.rmtree(tmp, ignore_errors=True)
                raise SnapshotError(f"cannot write the snapshot: {_cause(exc)}") from None
            except BaseException:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
            info = SnapshotInfo(
                run_id=run_id,
                flow_id=flow_id,
                taken_at=manifest.taken_at,
                files=len(entries),
                bytes=sum(e.size for e in entries if e.copied),
            )
            span.set_attribute("pkpipe.snapshot.files", info.files)
            span.set_attribute("pkpipe.snapshot.bytes", info.bytes)
        self.index.snapshot_upsert(
            {
                "run_id": run_id,
                "flow_id": flow_id,
                "taken_at": manifest.taken_at,
                "bytes": info.bytes,
                "files": info.files,
                "status": "kept",
            }
        )
        with contextlib.suppress(Exception):  # retention is housekeeping: never fails a run
            self.prune()
        return info

    def finish(self, run_id: str) -> SnapshotInfo | None:
        """Record what the run left different from the snapshot (``post.json``, FR-003). ``None`` when the
        run took no snapshot (a read-only flow). On a resume it replaces the earlier one."""
        base = self._snapshot_dir(run_id)
        if not (base / MANIFEST).is_file():
            return None
        _, manifest = self._load(run_id)
        scope = SnapshotScope(tuple(manifest.scope), tuple(manifest.managed), tuple(manifest.exclude))
        with tool_span("snapshot.finish") as span:
            span.set_attribute("tool.server", "pkpipe-snapshots")
            span.set_attribute("pkpipe.snapshot.run_id", run_id)
            try:
                now = {e.path: e for e in self._scan(scope)}
                entries = diff_manifests({e.path: e for e in manifest.files}, now)
                _write_json(
                    base / POST,
                    PostManifest(
                        run_id=run_id,
                        finished_at=self._now().isoformat(timespec="seconds"),
                        entries=entries,
                    ),
                )
            except OSError as exc:
                raise SnapshotError(f"cannot write the post manifest: {_cause(exc)}") from None
            span.set_attribute("pkpipe.snapshot.changed_files", len(entries))
        return self._info(run_id, manifest, finished=True)

    # -- restore -----------------------------------------------------------------------------------------
    def plan_restore(self, run_id: str, *, force: bool = False) -> RestorePlan:
        """The dry run: what a restore would do, changing nothing (FR-005, US2 scenario 1)."""
        base, manifest = self._load(run_id)
        post = self._load_post(base, run_id)
        if post is None:
            raise SnapshotError(
                f"run '{run_id}' has no post manifest: it has not finished (or stopped hard); "
                "let it finish or fail first"
            )
        pre = {e.path: e for e in manifest.files}
        return build_plan(pre, post.entries, self._current_sha, force=force, run_id=run_id)

    def apply_restore(self, run_id: str, *, force: bool = False) -> RestorePlan:
        """Apply the plan after a snapshot of the current state (a restore is undoable too). Nothing is
        deleted: removed files go to ``trash/``, ``--force`` saves the bytes it overwrites there first."""
        base, manifest = self._load(run_id)
        plan = self.plan_restore(run_id, force=force)
        if not plan.actionable:  # SC-003: a second restore changes nothing and takes no snapshot
            plan.applied = True
            return plan
        post = self._load_post(base, run_id)
        assert post is not None
        post_sha = {e.path: e.sha256 for e in post.entries}
        pre = {e.path: e for e in manifest.files}
        scope = SnapshotScope(tuple(manifest.scope), tuple(manifest.managed), tuple(manifest.exclude))
        safety = f"restore-{run_id[:70]}-{self._now().strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(2)}"
        with tool_span("snapshot.restore") as span:
            span.set_attribute("tool.server", "pkpipe-snapshots")
            span.set_attribute("pkpipe.snapshot.run_id", run_id)
            self._take(safety, "restore", scope)  # a failing safety snapshot stops the restore
            plan.safety_snapshot = safety
            for step in plan.steps:
                if step.verb == "skip":
                    step.outcome = f"skipped:{step.reason}"
                    continue
                step.outcome = self._apply_step(step, base, pre, post_sha, force)
            self.finish(safety)
            plan.applied = True
            counts = {
                "done": sum(1 for s in plan.steps if s.outcome == "done"),
                "skipped": sum(1 for s in plan.steps if s.outcome != "done"),
            }
            span.set_attribute("pkpipe.snapshot.restored_files", counts["done"])
            span.set_attribute("pkpipe.snapshot.skipped_files", counts["skipped"])
        if counts["done"]:
            self.index.snapshot_update(
                run_id, status="restored", reverted=1, restored_at=self._now().isoformat(timespec="seconds")
            )
        return plan

    def _apply_step(
        self,
        step: RestoreStep,
        base: Path,
        pre: Mapping[str, FileEntry],
        post_sha: Mapping[str, str | None],
        force: bool,
    ) -> str:
        """One step; its precondition is checked again right before it acts (the owner or Obsidian may have
        changed the file since the plan was made). Returns ``done`` or ``skipped:<reason>``."""
        try:
            if step.verb == "restore" and step.path:
                target = self._vault_path(step.path)
                cur = self._current_sha(step.path)
                if not force and cur not in (None, pre[step.path].sha256, post_sha.get(step.path)):
                    return "skipped:changed_during_restore"
                copy = base / FILES / step.path
                if not copy.is_file() or _hash_file(copy)[0] != pre[step.path].sha256:
                    return "skipped:snapshot_copy_damaged"
                if step.save_current and cur is not None:
                    self._to_trash(base, step.path, keep_original=True)
                self._write_restored(target, copy.read_bytes(), pre[step.path].mtime)
                return "done"
            if step.verb == "remove" and step.path:
                cur = self._current_sha(step.path)
                if cur is None:
                    return "done"
                if not force and cur != post_sha.get(step.path):
                    return "skipped:changed_during_restore"
                self._to_trash(base, step.path, keep_original=False)
                return "done"
            if step.verb == "move_back" and step.from_path and step.to:
                src = self._vault_path(step.from_path)
                dst = self._vault_path(step.to)
                if self._current_sha(step.from_path) != post_sha.get(step.from_path):
                    return "skipped:changed_during_restore"
                dst.parent.mkdir(parents=True, exist_ok=True)
                with dir_lock(dst.parent):
                    try:  # link + unlink: never overwrites a note that appeared meanwhile
                        os.link(src, dst, follow_symlinks=False)
                    except FileExistsError:
                        return "skipped:destination_exists"
                    os.unlink(src)
                return "done"
        except OSError as exc:
            return f"skipped:os_error ({_cause(exc)})"
        except SnapshotError as exc:
            return f"skipped:{exc}"
        return "skipped:unknown_step"

    @staticmethod
    def _write_restored(target: Path, data: bytes, mtime: int) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with dir_lock(target.parent):
            fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                if target.exists():
                    os.chmod(tmp, target.stat().st_mode & 0o777)
                os.utime(tmp, (mtime, mtime))
                os.replace(tmp, target)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise

    def _to_trash(self, base: Path, rel: str, *, keep_original: bool) -> Path:
        """Copy the vault file ``rel`` into ``trash/`` (never overwriting an earlier one); unless
        ``keep_original`` remove it from the vault after the copy is verified."""
        source = self._vault_path(rel)
        dest = base / TRASH / rel
        n = 0
        while dest.exists():
            n += 1
            dest = base / TRASH / f"{rel[:-3]}~{n}.md"
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = source.read_bytes()
        dest.write_bytes(data)
        os.chmod(dest, 0o600)
        if hashlib.sha256(dest.read_bytes()).hexdigest() != hashlib.sha256(data).hexdigest():
            raise SnapshotError("the trash copy does not match; the file stays in the vault")
        if not keep_original:
            with dir_lock(source.parent):
                os.unlink(source)
        return dest

    # -- listing -----------------------------------------------------------------------------------------
    def _info(self, run_id: str, manifest: Manifest, *, finished: bool | None = None) -> SnapshotInfo:
        row = self.index.snapshot_get(run_id) or {}
        if finished is None:
            finished = (self._snapshot_dir(run_id) / POST).is_file()
        return SnapshotInfo(
            run_id=run_id,
            flow_id=manifest.flow_id,
            taken_at=manifest.taken_at,
            files=len(manifest.files),
            bytes=sum(e.size for e in manifest.files if e.copied),
            status=row.get("status", "kept"),
            finished=finished,
            reverted=bool(row.get("reverted", 0)),
            restored_at=row.get("restored_at"),
        )

    def _run_dirs(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(
            p
            for p in self.directory.iterdir()
            if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")
        )

    def list(self) -> list[SnapshotInfo]:
        """Every snapshot, newest first: on disk, plus the ``pruned`` rows of the index."""
        out: dict[str, SnapshotInfo] = {}
        for path in self._run_dirs():
            try:
                _, manifest = self._load(path.name)
            except SnapshotError:
                continue
            out[path.name] = self._info(path.name, manifest)
        for row in self.index.snapshot_list():
            if row["run_id"] not in out and row["status"] == "pruned":
                out[row["run_id"]] = SnapshotInfo(
                    run_id=row["run_id"],
                    flow_id=row["flow_id"],
                    taken_at=row["taken_at"],
                    files=row["files"],
                    bytes=row["bytes"],
                    status="pruned",
                    reverted=bool(row["reverted"]),
                    restored_at=row["restored_at"],
                )
        return sorted(out.values(), key=lambda i: (i.taken_at, i.run_id), reverse=True)

    def show(self, run_id: str) -> dict[str, Any]:
        """The snapshot's info, its scope and what the run changed (paths: for the owner's terminal only)."""
        base, manifest = self._load(run_id)
        post = self._load_post(base, run_id)
        return {
            "info": self._info(run_id, manifest).model_dump(mode="json"),
            "flow_id": manifest.flow_id,
            "policy_digest": manifest.policy_digest,
            "scope": manifest.scope,
            "managed": manifest.managed,
            "exclude": manifest.exclude,
            "not_copied": [e.path for e in manifest.files if not e.copied],
            "changes": (
                [e.model_dump(mode="json", by_alias=True, exclude_none=True) for e in post.entries]
                if post
                else None
            ),
        }

    # -- retention ---------------------------------------------------------------------------------------
    def prune(self) -> dict[str, Any]:
        """Remove snapshots older than ``retention_days`` and, while the total exceeds ``max_bytes``, the
        oldest ones; the newest snapshot of each flow always stays (FR-006)."""
        pruned: list[str] = []
        freed = 0
        if not self.directory.is_dir():
            return {"pruned": pruned, "freed_bytes": 0, "kept": 0}
        for stray in self.directory.glob(".tmp-*"):  # a snapshot that was being written when the process died
            with contextlib.suppress(OSError):
                if (
                    stray.is_dir()
                    and not stray.is_symlink()
                    and self._now().timestamp() - stray.stat().st_mtime > 86400
                ):
                    shutil.rmtree(stray, ignore_errors=True)
        infos = []
        for path in self._run_dirs():
            with contextlib.suppress(SnapshotError):
                _, manifest = self._load(path.name)
                infos.append(self._info(path.name, manifest))
        newest: dict[str, str] = {}
        for info in sorted(infos, key=lambda i: (i.taken_at, i.run_id)):
            newest[info.flow_id] = info.run_id
        protected = set(newest.values())
        cutoff = self._now() - timedelta(days=self.retention_days)
        removable = [
            i for i in sorted(infos, key=lambda i: (i.taken_at, i.run_id)) if i.run_id not in protected
        ]
        doomed = [i for i in removable if datetime.fromisoformat(i.taken_at) < cutoff]
        total = sum(i.bytes for i in infos) - sum(i.bytes for i in doomed)
        for info in removable:
            if total <= self.max_bytes:
                break
            if info not in doomed:
                doomed.append(info)
                total -= info.bytes
        for info in doomed:
            self._remove(info.run_id)
            self.index.snapshot_update(info.run_id, status="pruned")
            pruned.append(info.run_id)
            freed += info.bytes
        return {"pruned": pruned, "freed_bytes": freed, "kept": len(infos) - len(pruned)}

    def _remove(self, run_id: str) -> None:
        path = self._snapshot_dir(run_id)
        if path.is_symlink() or path.parent != self.directory:
            raise SnapshotError("refusing to remove a snapshot folder that is not a plain folder")
        shutil.rmtree(path)

    # -- doctor ------------------------------------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        """The snapshot directory for ``pkpipe doctor``: free space, number, size and newest snapshot."""
        return directory_summary(self.directory, retention_days=self.retention_days, max_bytes=self.max_bytes)


def directory_summary(
    directory: Path, *, retention_days: int = DEFAULT_RETENTION_DAYS, max_bytes: int = DEFAULT_MAX_BYTES
) -> dict[str, Any]:
    """What is on disk in the snapshot directory (no vault, no app DB needed): free space, number and size of
    the snapshots and the newest one. Reads the manifests only; a damaged one is not counted."""
    rows: list[tuple[str, str, str, int]] = []
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if not path.is_dir() or path.is_symlink() or path.name.startswith("."):
                continue
            try:
                m = Manifest.model_validate_json((path / MANIFEST).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rows.append((m.taken_at, path.name, m.flow_id, sum(e.size for e in m.files if e.copied)))
    probe = directory
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    rows.sort(reverse=True)
    newest = rows[0] if rows else None
    return {
        "directory": str(directory),
        "free_bytes": shutil.disk_usage(probe).free,
        "snapshots": len(rows),
        "bytes": sum(r[3] for r in rows),
        "newest": {"run_id": newest[1], "flow_id": newest[2], "taken_at": newest[0]} if newest else None,
        "max_bytes": max_bytes,
        "retention_days": retention_days,
    }
