"""Vault operations behind the MCP tools; every path goes through ``VaultPolicy`` (spec 004 FR-001..003)."""

from __future__ import annotations

import contextlib
import functools
import os
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pkpipe.domain.inbox_list import archive_text, remove_entries
from pkpipe.domain.journal_todos import DEFAULT_HEADING, JournalTodoError, replace_block
from pkpipe.security import quarantine
from pkpipe.telemetry import set_span_attributes, tool_span

from . import improvements, lists, tool_candidates
from .atomic import dir_lock, update_text
from .errors import ImageRejected, NoteExists, NoteNotFound, PathRejected, VaultError, WriteConflict
from .notes import append_text, dump_front_matter, jsonable, parse_note, render_note
from .policy import VaultPolicy, is_conflict_name
from .surface import DATA_PREAMBLE, TriageSurface


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
                "update_tool_candidates",
                "inbox_remove_entries",
            ) and isinstance(rows, list):
                span.set_attribute("pkpipe.vault.rows", len(rows))
                ids = [r.get("item_id") or r.get("id") for r in rows if isinstance(r, dict)]
                if len(ids) == 1 and isinstance(ids[0], str):
                    set_span_attributes(span, item_id=ids[0])
            return fn(self, *args, **kwargs)

    return wrapper


#: Notes ``unreviewed_notes`` looks at per call (a folder of an owner can be large).
MAX_SCANNED_NOTES = 5000

IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def image_signature_ok(media_type: str, head: bytes) -> bool:
    """The first bytes of a file say it is the image type its extension claims (no decoding here)."""
    if media_type == "image/jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if media_type == "image/png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if media_type == "image/webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return False


MAX_NOTES = 60
MAX_ENTRIES = 200
MAX_REPLANS = 3


class _Replan(Exception):
    """The list changed between planning and the locked rewrite."""


ITEM_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class Vault:
    def __init__(
        self,
        policy: VaultPolicy,
        *,
        today: Callable[[], date] = date.today,
        now: Callable[[], datetime] = datetime.now,
    ):
        self.policy = policy
        self._today = today
        self._now = now

    # -- images (spec 021) ---------------------------------------------------------------------------------

    @_traced
    def read_image(self, name: str) -> dict[str, Any]:
        """Read one embedded image of ``attachments_dir`` (read only, own allowlist entry) by its basename.

        Refusals have a stable code: ``bad_name`` (path segments, hidden or odd names, a symlink),
        ``bad_type`` (extension not ``jpg jpeg png webp gif`` or content that is not that image type),
        ``not_found`` and ``too_large`` (``max_image_bytes``). Returns ``{name, media_type, size,
        data_base64}``; the bytes are untrusted content and never decoded here."""
        import base64

        if (
            not isinstance(name, str)
            or not name.strip()
            or name != name.strip()
            or any(c in name for c in ("/", "\\", "\x00", ":"))
            or name.startswith((".", "~"))
        ):
            raise ImageRejected("bad_name", "give the file name only")
        media_type = IMAGE_TYPES.get(Path(name).suffix.lower())
        if media_type is None:
            raise ImageRejected("bad_type", "allowed: " + ", ".join(sorted(IMAGE_TYPES)))
        try:
            folder = self.policy.resolve(self.policy.layout.attachments_dir, "read_image", kind="folder")
        except PathRejected:
            raise ImageRejected("not_found", "the attachments folder is not readable") from None
        if not folder.is_dir():
            raise ImageRejected("not_found", "no such image")
        target = self._find_image(folder, name)
        if target is None:
            raise ImageRejected("not_found", "no such image")
        if target.is_symlink():
            raise ImageRejected("bad_name", "symbolic links are not read")
        try:
            inside = target.resolve().relative_to(folder.resolve())
        except ValueError:
            raise ImageRejected("bad_name", "the file lies outside the attachments folder") from None
        if unicodedata.normalize("NFC", inside.name) != unicodedata.normalize("NFC", name):
            raise ImageRejected("bad_name", "the file lies outside the attachments folder")
        size = target.stat().st_size
        if size > self.policy.max_image_bytes:
            raise ImageRejected("too_large", f"limit {self.policy.max_image_bytes} bytes")
        data = target.read_bytes()
        if not image_signature_ok(media_type, data[:16]):
            raise ImageRejected("bad_type", "the content is not the image type of the extension")
        return {
            "name": name,
            "media_type": media_type,
            "size": len(data),
            "data_base64": base64.b64encode(data).decode("ascii"),
        }

    @staticmethod
    def _find_image(folder: Path, name: str) -> Path | None:
        """The file called ``name`` directly in ``folder``, else the first match (sorted) in a real subfolder;
        symbolic links to folders are never followed."""
        direct = folder / name
        if direct.is_symlink() or direct.is_file():
            return direct
        want = unicodedata.normalize("NFC", name)  # macOS file names may be decomposed (NFD)
        for root, dirs, files in os.walk(folder, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (Path(root) / d).is_symlink())
            for f in sorted(files):
                if unicodedata.normalize("NFC", f) == want:
                    return Path(root) / f
        return None

    # -- notes -----------------------------------------------------------------------------------------------

    @_traced
    def list_folder(self, path: str) -> dict[str, Any]:
        folder = self.policy.resolve(path, "list_folder", kind="folder")
        if not folder.is_dir():
            raise NoteNotFound(f"folder not found: {path}")
        notes: list[str] = []
        folders: list[str] = []
        conflicts: list[str] = []
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

    @_traced
    def write_journal_todos(
        self, day: str, lines: list[str], heading: str = DEFAULT_HEADING
    ) -> dict[str, Any]:
        """Spec 024: set the pkpipe todo block of the daily journal of ``day`` (today or yesterday).

        The only journal write path. It replaces the text between the two markers below the heading (see
        :mod:`pkpipe.domain.journal_todos`) and changes no other byte; it never reads the journal out: the
        answer is the path, the number of lines and whether the note was created. A granted folder in
        ``allow.write_journal_todos`` is required (no default)."""
        if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise PathRejected("the day must be YYYY-MM-DD")
        try:
            parsed = date.fromisoformat(day)
        except ValueError:
            raise PathRejected("the day must be a real date") from None
        if abs((parsed - self._today()).days) > 1:
            raise PathRejected("the todo block is written for today or yesterday only")
        rel = self.policy.layout.journal_path(parsed)
        target = self._writable(rel, "write_journal_todos")
        created = not target.exists()
        try:
            update_text(target, lambda existing: replace_block(existing, heading, lines))
        except JournalTodoError as exc:
            raise PathRejected(str(exc)) from None
        return {"path": rel, "lines": len(lines), "created": created}

    @_traced
    def unreviewed_notes(self, folder: str, category: str) -> dict[str, Any]:
        """Spec 024: how many notes the pipeline wrote for ``category`` (front matter ``pkpipe_item`` and
        ``category``) below ``folder`` the owner has not marked ``reviewed: true``, and the newest one.
        Counts and one path only: no note text, title or front matter is returned."""
        if not isinstance(category, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", category):
            raise PathRejected("the category must be a category id")
        root = self.policy.resolve(folder, "unreviewed_notes", kind="folder")
        if not root.is_dir():
            return {"count": 0, "newest": None}
        count, newest, newest_mtime = 0, None, -1.0
        for scanned, entry in enumerate(sorted(root.rglob("*.md"))):
            if scanned >= MAX_SCANNED_NOTES:
                break
            if is_conflict_name(entry.name) or any(p.startswith(".") for p in entry.relative_to(root).parts):
                continue
            if not entry.is_file() or entry.is_symlink():
                continue
            front = parse_note(entry.read_text(encoding="utf-8")).front_matter
            if not front.get("pkpipe_item") or front.get("category") != category:
                continue
            if str(front.get("reviewed", "")).strip().lower() in ("true", "yes"):
                continue
            count += 1
            mtime = entry.stat().st_mtime
            if mtime > newest_mtime:
                newest, newest_mtime = self.policy.rel(entry), mtime
        return {"count": count, "newest": newest}

    @_traced
    def inbox_remove_entries(self, entries: list[str]) -> dict[str, Any]:
        """Spec 020 FR-005: remove consumed entries from the declared list file and append them to the dated
        archive file. The only new write path: it touches nothing but those two files; entries are matched by
        their raw text (never by line number) and every other byte of the list stays."""
        layout = self.policy.layout
        if not layout.inbox_list_file:
            raise PathRejected("no inbox_list_file is configured in the layout")
        if not isinstance(entries, list) or not entries or len(entries) > MAX_ENTRIES:
            raise PathRejected(f"give a list of 1 to {MAX_ENTRIES} raw entries")
        if not all(isinstance(e, str) and e.strip() for e in entries):
            raise PathRejected("every entry must be a non-empty string")
        target = self.policy.resolve(layout.inbox_list_file, "inbox_remove_entries")
        if is_conflict_name(target.name) or self.policy.in_quarantine(target):
            raise PathRejected("refusing to rewrite the list file there")
        if self._is_managed_other_than(target):
            raise PathRejected("the list file must not be one of the managed files")
        if not target.is_file():
            raise NoteNotFound(f"inbox list not found: {layout.inbox_list_file}")
        config = layout.inbox_list.to_config()
        day = self._today()  # once per call: one dated archive, one header
        archive_rel = layout.inbox_archive_path(day)
        archive = self.policy.resolve(archive_rel, "inbox_remove_entries")
        if is_conflict_name(archive.name) or self.policy.in_quarantine(archive):
            raise PathRejected("refusing to write the archive there")
        if self._is_managed(archive_rel, archive) or _same_file(archive, target):
            raise PathRejected("the archive must not be the list file or another managed file")
        # The locked rewrite of the list never removes an entry that is not in the archive: when the list
        # changed since the plan was made, the plan is redone. Archive first, so a crash leaves an entry in
        # both files (at least once), never in neither (the snapshot is the net).
        archived: Counter[str] = Counter()
        outcome: dict[str, Any] = {}
        for _ in range(MAX_REPLANS):
            planned = remove_entries(target.read_text(encoding="utf-8"), entries, config)
            fresh = list((Counter(planned.removed) - archived).elements())
            if fresh:
                update_text(archive, self._archive_appender(fresh, day))
                archived.update(fresh)
            outcome.clear()

            def transform(text: str | None) -> str:
                result = remove_entries(text or "", entries, config)
                if Counter(result.removed) - archived:  # the list changed: something would go unarchived
                    raise _Replan
                outcome["result"] = result
                return result.text

            try:
                update_text(target, transform)
            except _Replan:
                continue
            result = outcome["result"]
            return {
                "removed": result.removed,
                "not_found": result.not_found,
                "list": layout.inbox_list_file,
                "archive": archive_rel if archived else None,
            }
        raise WriteConflict(f"{layout.inbox_list_file} kept changing while removing entries; retry")

    def _archive_appender(self, raws: list[str], day: date) -> Callable[[str | None], str]:
        return lambda existing: archive_text(existing, raws, day)

    def _is_managed_other_than(self, target: Path) -> bool:
        """Whether ``target`` is one of the maintained table files (not the list file itself)."""
        key = _fold(self.policy.rel(target))
        p = self.policy
        for managed in (
            p.triage_file,
            p.layout.triage_data,
            p.processing_list,
            p.improvement_file,
            p.tool_candidates_file,
        ):
            if key == _fold(managed) or _same_file(target, self.policy.root / managed):
                return True
        return False

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

    # -- the triage table (spec 023: it lives in the triage file or, on the ``web`` surface, in its data file)

    def _surface(self, tool: str) -> TriageSurface:
        layout = self.policy.layout
        return TriageSurface(
            surface=self.policy.triage_surface,
            page=self.policy.resolve(layout.triage_file, tool),
            data=self.policy.resolve(layout.triage_data, tool),
            page_rel=layout.triage_file,
            data_rel=layout.triage_data,
            public_url=self.policy.ui_public_url,
            now=self._now,
        )

    def _table(self, tool: str) -> tuple[TriageSurface, Path, str]:
        """The surface, the file that holds the table now and the preamble a new table there gets."""
        surface = self._surface(tool)
        path = surface.holder()
        return surface, path, (DATA_PREAMBLE if path == surface.data else lists.TRIAGE_PREAMBLE)

    def triage_migration_plan(self) -> dict[str, Any] | None:
        """What the next triage write moves between the triage file and its data file (dry run), or None."""
        plan = self._surface("read_triage").plan()
        return plan.as_dict() if plan else None

    @_traced
    def read_triage(self) -> dict[str, Any]:
        _, path, _ = self._table("read_triage")
        return lists.read_triage(path)

    @_traced
    def update_triage(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """The pipeline's write path. First moves the table if the surface was switched (spec 023), then
        applies the rows; an empty list is the maintenance call (migration, columns). The generated page of
        the ``web`` surface is refreshed with the run time."""
        surface = self._surface("update_triage")
        plan = surface.migrate()
        _, path, preamble = self._table("update_triage")
        result = lists.update_triage(path, rows, preamble=preamble)
        surface.refresh_page(stamp=True)
        return {**result, "migration": plan.as_dict() if plan else None}

    # Owner channels only (UI, app MCP): not registered on the vault MCP server that sessions can reach.

    def _owner_write(self, fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
        surface, path, preamble = self._table("update_triage")
        result = fn(path, *args, preamble=preamble, **kwargs)
        surface.refresh_page(stamp=False)
        return result

    @_traced
    def owner_decide(
        self,
        item_id: str,
        *,
        confirm: bool | None = None,
        override: str | None = None,
        categories: Any = None,
    ) -> dict[str, Any]:
        return self._owner_write(
            lists.owner_decide, item_id, confirm=confirm, override=override, categories=categories
        )

    def _processing_path(self) -> Path:
        return self.policy.resolve(self.policy.processing_list, "upsert_processing_list")

    @_traced
    def failed_items(self) -> dict[str, Any]:
        """Item ids and categories of the processing-list lines marked ``failed`` (spec 027). Read only: no
        note, resource or text is returned, so the run report and the owner channels may use it."""
        failed = lists.failed_processing_items(self._processing_path())
        return {"count": len(failed), "items": [{"item_id": i, "category": c} for i, c in failed.items()]}

    @_traced
    def owner_retry(self, item_id: str, *, retries: int, max_retries: int) -> dict[str, Any]:
        """Owner decision (spec 027): a processed item with a failed result goes back to ``released``. Owner
        channels only (no MCP tool of the vault server calls this): the one way out of ``processed``."""
        _, path, preamble = self._table("update_triage")
        result = lists.retry_item(
            path,
            self._processing_path(),
            item_id,
            retries=retries,
            max_retries=max_retries,
            preamble=preamble,
        )
        self._surface("update_triage").refresh_page(stamp=False)
        return result

    @_traced
    def owner_comment(self, item_id: str, comment: str) -> dict[str, Any]:
        """The owner's comment of one row (spec 022 clarification 1, spec 023): owner channels only."""
        return self._owner_write(lists.owner_comment, item_id, comment)

    @_traced
    def owner_release_quarantine(self, item_id: str) -> dict[str, Any]:
        """Owner decision (spec 009 US3): the row goes back to ``pending`` and the quarantined note back to
        its INBOX path. ``content`` (the restored text, for the eval candidate) must not be passed on to an
        agent; :class:`pkpipe.api.services.AppServices` drops it."""
        self._restore_quarantined(item_id, dry_run=True)  # refuse before the row changes
        row = self._owner_write(lists.release_quarantine, item_id)
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

    @_traced
    def read_tool_candidates(self) -> dict[str, Any]:
        path = self.policy.resolve(self.policy.tool_candidates_file, "read_tool_candidates")
        return tool_candidates.read_tool_candidates(path)

    @_traced
    def update_tool_candidates(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        path = self.policy.resolve(self.policy.tool_candidates_file, "update_tool_candidates")
        return tool_candidates.update_tool_candidates(path, rows)


def _same_file(a: Path, b: Path) -> bool:
    with contextlib.suppress(OSError):
        return a.exists() and b.exists() and os.path.samefile(a, b)
    return False


def _fold(rel: str) -> str:
    return unicodedata.normalize("NFC", rel).casefold()


__all__ = ["Vault", "VaultError"]
