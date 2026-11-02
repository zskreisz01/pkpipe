"""Vault operations behind the MCP tools; every path goes through ``VaultPolicy`` (spec 004 FR-001..003)."""

from __future__ import annotations

import contextlib
import functools
import os
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pkpipe.security import quarantine
from pkpipe.telemetry import set_span_attributes, tool_span

from . import improvements, lists
from .atomic import dir_lock, update_text
from .errors import NoteExists, NoteNotFound, PathRejected, VaultError
from .notes import append_text, dump_front_matter, jsonable, parse_note, render_note
from .policy import VaultPolicy, is_conflict_name


def _traced(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Constitution X: one ``tool <name>`` span per vault operation (the MCP tools call these methods).

    Only the tool name, the item id of a single-row upsert, and a row count are recorded. Paths, bodies,
    front matter and row values are never attached, so note content cannot reach a trace.
    """

    @functools.wraps(fn)
    def wrapper(self: Vault, *args: Any, **kwargs: Any) -> Any:
        with tool_span(fn.__name__) as span:
            span.set_attribute("tool.server", "obsidian-vault")
            rows = kwargs.get("rows", args[0] if args else None)
            if fn.__name__ in (
                "update_triage",
                "upsert_processing_list",
                "update_improvements",
            ) and isinstance(rows, list):
                span.set_attribute("pkpipe.vault.rows", len(rows))
                ids = [r.get("item_id") or r.get("id") for r in rows if isinstance(r, dict)]
                if len(ids) == 1 and isinstance(ids[0], str):
                    set_span_attributes(span, item_id=ids[0])
            return fn(self, *args, **kwargs)

    return wrapper


MAX_NOTES = 60
ITEM_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class Vault:
    def __init__(self, policy: VaultPolicy):
        self.policy = policy

    # -- notes -----------------------------------------------------------------------------------------------

    @_traced
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

    @_traced
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

    @_traced
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

    @_traced
    def append_note(self, path: str, text: str) -> dict[str, Any]:
        target = self._writable(path, "append_note")

        def transform(existing_text: str | None) -> str:
            if existing_text is None:
                raise NoteNotFound(f"note not found: {path}")
            return append_text(existing_text, text)

        update_text(target, transform)
        return {"path": path}

    @_traced
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

    @_traced
    def read_notes(self, paths: list[str]) -> dict[str, Any]:
        """Read several notes at once (journal days, project notes). A missing note is listed in ``missing``,
        not an error; a path outside the allowlist rejects the whole call."""
        if not isinstance(paths, list) or len(paths) > MAX_NOTES:
            raise PathRejected(f"give a list of at most {MAX_NOTES} paths")
        targets = [(p, self.policy.resolve(p, "read_notes")) for p in paths]
        notes, missing = [], []
        for path, target in targets:
            if not target.is_file() or is_conflict_name(target.name):
                missing.append(path)
                continue
            note = parse_note(target.read_text(encoding="utf-8"))
            notes.append({"path": path, "front_matter": jsonable(note.front_matter), "body": note.body})
        return {"notes": notes, "missing": missing}

    def _writable(self, path: str, tool: str) -> Path:
        target = self.policy.resolve(path, tool)
        if is_conflict_name(target.name):
            raise PathRejected("refusing to write a sync conflict file")
        if self.policy.in_quarantine(target):
            raise PathRejected("the quarantine folder is written only by quarantine_note (spec 009)")
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

    @_traced
    def read_triage(self) -> dict[str, Any]:
        path = self.policy.resolve(self.policy.triage_file, "read_triage")
        return lists.read_triage(path)

    @_traced
    def update_triage(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        path = self.policy.resolve(self.policy.triage_file, "update_triage")
        return lists.update_triage(path, rows)

    # Owner channels only (UI, app MCP): not registered on the vault MCP server that sessions can reach.

    @_traced
    def owner_decide(
        self,
        item_id: str,
        *,
        confirm: bool | None = None,
        override: str | None = None,
        categories: set[str] | frozenset[str] | None = None,
    ) -> dict[str, Any]:
        path = self.policy.resolve(self.policy.triage_file, "update_triage")
        return lists.owner_decide(path, item_id, confirm=confirm, override=override, categories=categories)

    @_traced
    def owner_release_quarantine(self, item_id: str) -> dict[str, Any]:
        """Owner decision (spec 009 US3): the row goes back to ``pending`` and the quarantined note back to
        its INBOX path. ``content`` (the restored text, for the eval candidate) must not be passed on to an
        agent; :class:`pkpipe.api.services.AppServices` drops it."""
        path = self.policy.resolve(self.policy.triage_file, "update_triage")
        self._restore_quarantined(item_id, dry_run=True)  # refuse before the row changes
        row = lists.release_quarantine(path, item_id)
        restore = self._restore_quarantined(item_id, dry_run=False)
        return {**row, **restore}

    # -- quarantine (spec 009) -------------------------------------------------------------------------

    def _quarantine_target(self, item_id: str) -> Path:
        if not isinstance(item_id, str) or not ITEM_ID_RE.fullmatch(item_id):
            raise PathRejected("item_id must be 1-64 letters, digits, '-' or '_'")
        folder = self.policy.root / self.policy.quarantine_dir
        target = (folder / f"{item_id}.md").resolve()
        if not self.policy.in_quarantine(target) or target.parent != folder.resolve():
            raise PathRejected("quarantine path escapes the quarantine folder")
        return target

    @_traced
    def quarantine_note(
        self,
        item_id: str,
        path: str | None = None,
        flags: list[dict[str, Any]] | None = None,
        run_id: str = "",
        flagged_at: str = "",
    ) -> dict[str, Any]:
        """Move a flagged note into the quarantine folder in inert form (code block, ``security_flag`` front
        matter with flag names and reason codes only). Idempotent: a re-run after the note moved keeps the
        quarantined copy. Without ``path`` (an item that came from a URL only) a metadata note is written."""
        target = self._quarantine_target(item_id)
        source: Path | None = None
        if path:
            source = self.policy.resolve(path, "quarantine_note")
            if self.policy.in_quarantine(source) or self._is_managed(path, source):
                raise PathRejected("this note cannot be quarantined")
        raw = source.read_bytes().decode("utf-8") if source is not None and source.is_file() else None
        original = raw.replace("\r\n", "\n") if raw is not None else None
        if original is None and target.is_file():
            return {"path": self.policy.rel(target), "original_path": path, "moved": False}
        front = quarantine.quarantine_front_matter(
            item_id=item_id,
            original_path=path,
            flags=[f for f in flags or [] if isinstance(f, dict)],
            run_id=str(run_id)[:64],
            flagged_at=str(flagged_at)[:40],
        )
        if raw is not None:
            front["original_format"] = quarantine.text_format(raw)  # for the exact restore
        text = dump_front_matter(front) + quarantine.quarantine_body(item_id, original)
        target.parent.mkdir(parents=True, exist_ok=True)
        update_text(target, lambda _existing: text)
        if source is not None and original is not None:
            source.unlink()
        return {"path": self.policy.rel(target), "original_path": path, "moved": original is not None}

    def _restore_quarantined(self, item_id: str, *, dry_run: bool) -> dict[str, Any]:
        """Check (``dry_run``) or do the move back of a quarantined note to its original path."""
        target = self._quarantine_target(item_id)
        if not target.is_file():
            return {"restored": None, "content": None, "flags": []}
        note = parse_note(target.read_text(encoding="utf-8"))
        original_path = note.front_matter.get("original_path")
        text = quarantine.unwrap_inert(note.body)
        flag_info = note.front_matter.get("security_flag")
        flags = [str(f) for f in (flag_info or {}).get("flags") or []] if isinstance(flag_info, dict) else []
        if not original_path or text is None:
            if not dry_run:
                target.unlink()
            return {"restored": None, "content": text, "flags": flags}
        dest = self.policy.resolve(str(original_path), "quarantine_note")
        if self.policy.in_quarantine(dest):
            raise PathRejected("invalid original path in the quarantine note")
        if dest.exists():
            raise NoteExists(f"cannot restore: {original_path} exists again; move it away first")
        text = quarantine.restore_format(text, note.front_matter.get("original_format"))
        if not dry_run:
            update_text(dest, lambda _existing: text)
            target.unlink()
        return {"restored": original_path, "content": text, "flags": flags}

    @_traced
    def upsert_processing_list(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        path = self.policy.resolve(self.policy.processing_list, "upsert_processing_list")
        return lists.upsert_processing_list(path, rows)

    @_traced
    def read_improvements(self) -> dict[str, Any]:
        path = self.policy.resolve(self.policy.improvement_file, "read_improvements")
        return improvements.read_improvements(path)

    @_traced
    def update_improvements(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        path = self.policy.resolve(self.policy.improvement_file, "update_improvements")
        return improvements.update_improvements(path, rows)


def _fold(rel: str) -> str:
    return unicodedata.normalize("NFC", rel).casefold()


__all__ = ["Vault", "VaultError"]
