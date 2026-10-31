"""Vault operations behind the MCP tools; every path goes through ``VaultPolicy`` (spec 004 FR-001..003)."""

from __future__ import annotations

import contextlib
import os
import unicodedata
from pathlib import Path
from typing import Any

from . import lists
from .atomic import dir_lock, update_text
from .errors import NoteExists, NoteNotFound, PathRejected, VaultError
from .notes import append_text, jsonable, parse_note, render_note
from .policy import VaultPolicy, is_conflict_name


class Vault:
    def __init__(self, policy: VaultPolicy):
        self.policy = policy

    # -- notes -----------------------------------------------------------------------------------------------

    def list_folder(self, path: str) -> dict[str, Any]:
        folder = self.policy.resolve(path, "list_folder", kind="folder")
        if not folder.is_dir():
            raise NoteNotFound(f"folder not found: {path}")
        notes, folders, conflicts = [], [], []
        for entry in sorted(folder.iterdir()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                folders.append(entry.name)
            elif entry.suffix == ".md":
                (conflicts if is_conflict_name(entry.name) else notes).append(entry.name)
        return {"path": path.rstrip("/"), "notes": notes, "folders": folders, "conflicts": conflicts}

    def read_note(self, path: str) -> dict[str, Any]:
        target = self.policy.resolve(path, "read_note")
        if not target.is_file():
            raise NoteNotFound(f"note not found: {path}")
        note = parse_note(target.read_text(encoding="utf-8"))
        return {
            "path": path,
            "front_matter": jsonable(note.front_matter),
            "front_matter_error": note.front_matter_error,
            "body": note.body,
            "conflict": is_conflict_name(target.name),
        }

    def write_note(
        self, path: str, body: str, front_matter: dict[str, Any] | None = None, overwrite: bool = False
    ) -> dict[str, Any]:
        target = self._writable(path, "write_note")

        def transform(existing_text: str | None) -> str:
            if existing_text is not None and not overwrite:
                raise NoteExists(f"note exists: {path} (pass overwrite=true to replace it)")
            existing = parse_note(existing_text) if existing_text is not None else None
            return render_note(body, front_matter, existing)

        update_text(target, transform)
        return {"path": path, "created": True}

    def append_note(self, path: str, text: str) -> dict[str, Any]:
        target = self._writable(path, "append_note")

        def transform(existing_text: str | None) -> str:
            if existing_text is None:
                raise NoteNotFound(f"note not found: {path}")
            return append_text(existing_text, text)

        update_text(target, transform)
        return {"path": path}

    def move_note(self, src: str, dst: str) -> dict[str, Any]:
        source = self._writable(src, "move_note")
        dest = self._writable(dst, "move_note")
        if not source.is_file():
            raise NoteNotFound(f"note not found: {src}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dir_lock(dest.parent):
            # link + unlink instead of rename: link fails if the destination appeared meanwhile (for example
            # created in Obsidian, which does not honour our lock), so a move can never overwrite a note.
            try:
                os.link(source, dest, follow_symlinks=False)
            except FileExistsError:
                raise NoteExists(f"destination exists: {dst}") from None
            except OSError:  # file system without hard links: best effort under the lock
                if dest.exists():
                    raise NoteExists(f"destination exists: {dst}") from None
                os.rename(source, dest)
                return {"from": src, "to": dst}
            os.unlink(source)
        return {"from": src, "to": dst}

    def _writable(self, path: str, tool: str) -> Path:
        target = self.policy.resolve(path, tool)
        if is_conflict_name(target.name):
            raise PathRejected("refusing to write a sync conflict file")
        if self._is_managed(path, target):
            raise PathRejected("this file is maintained by the triage/processing-list tools")
        return target

    def _is_managed(self, path: str, target: Path) -> bool:
        """Managed-file check that also holds on case-insensitive / normalising file systems (macOS APFS)."""
        if self.policy.is_managed(path):
            return True
        key = _fold(self.policy.rel(target))
        for managed in self.policy.managed_files:
            if key == _fold(managed):
                return True
            managed_path = self.policy.root / managed
            with contextlib.suppress(OSError):
                if target.exists() and managed_path.exists() and os.path.samefile(target, managed_path):
                    return True
        return False

    # -- lists -----------------------------------------------------------------------------------------------

    def read_triage(self) -> dict[str, Any]:
        path = self.policy.resolve(self.policy.triage_file, "read_triage")
        return lists.read_triage(path)

    def update_triage(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        path = self.policy.resolve(self.policy.triage_file, "update_triage")
        return lists.update_triage(path, rows)

    def upsert_processing_list(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        path = self.policy.resolve(self.policy.processing_list, "upsert_processing_list")
        return lists.upsert_processing_list(path, rows)


def _fold(rel: str) -> str:
    return unicodedata.normalize("NFC", rel).casefold()


__all__ = ["Vault", "VaultError"]
