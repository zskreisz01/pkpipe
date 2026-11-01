"""Publish a validated, gated draft to the config git repository, and roll it back (spec 014 US1).

Publishing writes the draft's files into the overlay working tree and makes **one commit that contains only
those files** (``git commit -- <paths>``), so unrelated uncommitted work in the overlay repository is never
swept in. The commit is local: pushing it is a separate owner action (nothing here talks to a remote).
Rollback is ``git revert`` of that commit, validated before it is committed.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from pkpipe.adapters.app_store_sqlite import AppStore
from pkpipe.api.workspace import ConfigWorkspace, WorkspaceError, editable_path
from pkpipe.engine.registry import ConfigError, load_effective

DEFAULT_IDENTITY = ("pkpipe", "pkpipe@localhost")


class PublishError(RuntimeError):
    """Git refused the publish or rollback; the working tree is restored."""


class GitRepo:
    """Thin ``git`` subprocess wrapper bound to the overlay directory."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
        proc = subprocess.run(
            ["git", "-C", str(self.directory), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
            check=False,
        )
        if check and proc.returncode != 0:
            raise PublishError(f"git {args[0]} failed: {(proc.stderr or proc.stdout).strip()[:500]}")
        return proc

    def toplevel(self) -> Path:
        proc = self.run("rev-parse", "--show-toplevel", check=False)
        if proc.returncode != 0:
            raise PublishError(f"'{self.directory}' is not inside a git repository")
        return Path(proc.stdout.strip()).resolve()

    def head(self) -> str | None:
        proc = self.run("rev-parse", "--verify", "-q", "HEAD", check=False)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def identity_args(self) -> list[str]:
        have = all(self.run("config", key, check=False).stdout.strip() for key in ("user.name", "user.email"))
        if have:
            return []
        return ["-c", f"user.name={DEFAULT_IDENTITY[0]}", "-c", f"user.email={DEFAULT_IDENTITY[1]}"]


class Publisher:
    def __init__(self, workspace: ConfigWorkspace, store: AppStore):
        self.workspace, self.store = workspace, store
        self._lock = threading.Lock()

    def _repo(self) -> tuple[GitRepo, Path, Path]:
        overlay = self.workspace.require_overlay()
        repo = GitRepo(overlay)
        top = repo.toplevel()
        pack = self.workspace.pack_dir.resolve()
        if top == pack or pack.is_relative_to(top):
            raise PublishError("the overlay must be its own git repository, separate from the public pack")
        return repo, top, overlay

    @staticmethod
    def _paths(repo: GitRepo, top: Path, rels: list[str]) -> list[str]:
        base = repo.directory.resolve().relative_to(top)
        return [(base / editable_path(r)).as_posix() for r in rels]

    @staticmethod
    def _restore(overlay: Path, backup: dict[str, str | None]) -> None:
        for rel, text in backup.items():
            target = overlay / rel
            if text is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")

    def publish(
        self, draft_id: str, files: dict[str, Any], *, summary: str, decision: dict[str, Any]
    ) -> dict:
        """Write the draft, commit it, record the publish. The caller has validated and gated the draft."""
        if not files:
            raise WorkspaceError("the draft has no changes")
        with self._lock:
            repo, top, overlay = self._repo()
            stale = self.workspace.conflicts(files)
            if stale:
                raise PublishError(f"changed on disk since the draft started: {', '.join(stale)}")
            rels = sorted(files)
            git_paths = self._paths(repo, top, rels)
            if repo.run("status", "--porcelain", "--", *git_paths, check=False).stdout.strip():
                raise PublishError(
                    "the overlay repository has uncommitted changes to these files; commit or discard them"
                )
            root = overlay.resolve()
            for rel in rels:  # never write through a symlink that leaves the overlay
                if not (overlay / rel).resolve().is_relative_to(root) or (overlay / rel).is_symlink():
                    raise PublishError(
                        f"'{rel}' resolves outside the overlay (symlink); refusing to write it"
                    )
            backup = {r: self.workspace.overlay_text(r) for r in rels}
            parent = repo.head()
            try:
                for rel in rels:
                    target = overlay / rel
                    content = files[rel]["content"]
                    if content is None:
                        target.unlink(missing_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        tmp = target.with_name(target.name + ".pkpipe-tmp")
                        tmp.write_text(content, encoding="utf-8")
                        os.replace(tmp, target)
                repo.run("add", "-A", "--", *git_paths)
                status = decision.get("status", "n/a")
                message = f"config: {summary}\n\npkpipe draft {draft_id}; eval gate: {status}"
                repo.run(*repo.identity_args(), "commit", "-q", "-m", message, "--", *git_paths)
            except Exception:
                self._restore(overlay, backup)
                repo.run("reset", "-q", "--", *git_paths, check=False)
                raise
            return self.store.add_publish(
                draft_id=draft_id,
                commit_sha=repo.head() or "",
                parent_sha=parent,
                kind="publish",
                files=rels,
                summary=summary,
                decision=decision,
            )

    def diff(self, publish_id: str) -> str:
        record = self.store.get_publish(publish_id)
        if record is None:
            raise KeyError(f"unknown publish '{publish_id}'")
        repo, top, _ = self._repo()
        paths = self._paths(repo, top, record["files"])
        return repo.run("show", "--format=", "--no-color", record["commit_sha"], "--", *paths).stdout

    def rollback(self, publish_id: str) -> dict:
        """``git revert`` of a publish commit; refused when it no longer applies or the result is invalid."""
        with self._lock:
            record = self.store.get_publish(publish_id)
            if record is None:
                raise KeyError(f"unknown publish '{publish_id}'")
            if record["kind"] != "publish" or record["reverted_by"]:
                raise PublishError("this publish cannot be rolled back (already reverted, or a rollback)")
            repo, top, overlay = self._repo()
            git_paths = self._paths(repo, top, record["files"])
            if repo.run("status", "--porcelain", "--", *git_paths, check=False).stdout.strip():
                raise PublishError("the overlay repository has uncommitted changes to these files")
            backup = {r: self.workspace.overlay_text(r) for r in record["files"]}
            parent = repo.head()

            def undo() -> None:
                repo.run("reset", "-q", "--", *git_paths, check=False)
                self._restore(overlay, backup)

            if repo.run("revert", "--no-commit", record["commit_sha"], check=False).returncode != 0:
                repo.run("revert", "--abort", check=False)
                undo()
                raise PublishError("the publish no longer reverts cleanly (later edits touch the same lines)")
            try:
                load_effective(self.workspace.pack_dir, overlay)
            except ConfigError as exc:
                undo()
                raise PublishError(f"the rolled-back config would be invalid: {exc.errors[0]}") from None
            try:
                message = f"config: roll back {record['commit_sha'][:10]} ({record['summary']})"
                repo.run(*repo.identity_args(), "commit", "-q", "-m", message, "--", *git_paths)
            except Exception:
                undo()
                raise
            new = self.store.add_publish(
                draft_id=None,
                commit_sha=repo.head() or "",
                parent_sha=parent,
                kind="rollback",
                files=record["files"],
                summary=f"roll back: {record['summary']}",
                decision={"status": "rollback", "of": publish_id},
            )
            self.store.mark_reverted(publish_id, new["publish_id"])
            return new
