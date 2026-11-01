"""Config drafts: what the UI may edit, how a draft is validated and diffed (spec 014 US1).

The editable layer is the private **overlay** (never the public pack). A draft is a set of file changes
``{path: {"content": str | None, "base_hash": str}}`` kept in the app DB. A draft file records the hash of the
overlay file it was started from; publish and further saves fail when that file changed on disk meanwhile
(optimistic lock, spec edge case). A draft is validated by materializing the overlay with the changes into a
temp directory and loading pack + that directory through the normal loader, so the UI and ``pkpipe validate``
agree.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pkpipe.engine.config_models import CONFIG_MODELS
from pkpipe.engine.registry import ConfigError, EffectiveConfig, load_effective
from pkpipe.engine.snapshot import ASSET_DIRS, ASSET_SUFFIXES, ConfigSnapshot, safe_asset_path

MAX_FILE_BYTES = 256 * 1024
ABSENT = "absent"


class WorkspaceError(ValueError):
    """The request is invalid (path not editable, file too large, no overlay)."""


class Conflict(WorkspaceError):
    """A file changed on disk since the draft started from it."""


def content_hash(text: str | None) -> str:
    return ABSENT if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def editable_path(rel: str) -> str:
    """Normalised config path the UI may edit; raises WorkspaceError for anything else.

    Allowed: ``<known config>.json``, ``flows/<id>.json`` and text assets below prompts/, instructions/,
    contexts/, schemas/, templates/. No absolute paths, ``..``, hidden folders or other file types.
    """
    safe = safe_asset_path(rel or "")
    if safe is None or any(p.startswith(".") for p in safe.split("/")):
        raise WorkspaceError(f"path '{rel}' is not allowed")
    parts = safe.split("/")
    if len(parts) == 1:
        stem, dot, suffix = parts[0].rpartition(".")
        if dot and suffix == "json" and stem in CONFIG_MODELS:
            return safe
    elif parts[0] == "flows" and len(parts) == 2 and parts[1].endswith(".json") and len(parts[1]) > 5:
        return safe
    elif parts[0] in ASSET_DIRS and safe.endswith(ASSET_SUFFIXES):
        return safe
    raise WorkspaceError(f"path '{rel}' is not an editable config file")


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    warnings: list[str]
    version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings, "version": self.version}


class ConfigWorkspace:
    def __init__(self, pack_dir: Path, overlay_dir: Path | None):
        self.pack_dir = Path(pack_dir)
        self.overlay_dir = Path(overlay_dir) if overlay_dir is not None else None

    @property
    def editable(self) -> bool:
        return self.overlay_dir is not None and self.overlay_dir.is_dir()

    def require_overlay(self) -> Path:
        if not self.editable:
            raise WorkspaceError("no private overlay configured (PKPIPE_OVERLAY_DIR): config is read-only")
        assert self.overlay_dir is not None
        return self.overlay_dir

    # -- reading --------------------------------------------------------------------------------------------
    def _layer_files(self, base: Path | None) -> set[str]:
        out: set[str] = set()
        if base is None or not base.is_dir():
            return out
        for p in base.rglob("*"):
            if not p.is_file() or p.is_symlink():
                continue
            rel = p.relative_to(base).as_posix()
            with contextlib.suppress(WorkspaceError):
                out.add(editable_path(rel))
        return out

    def list_files(self) -> list[dict[str, str]]:
        pack, overlay = self._layer_files(self.pack_dir), self._layer_files(self.overlay_dir)
        return [
            {
                "path": p,
                "source": "both" if p in pack and p in overlay else "overlay" if p in overlay else "pack",
            }
            for p in sorted(pack | overlay)
        ]

    def _read_layer(self, base: Path | None, rel: str) -> str | None:
        if base is None:
            return None
        target = base / rel
        if target.is_symlink() or not target.is_file():
            return None
        if not target.resolve().is_relative_to(base.resolve()):
            return None
        if target.stat().st_size > MAX_FILE_BYTES:
            raise WorkspaceError(f"'{rel}' is larger than {MAX_FILE_BYTES // 1024} KiB")
        return target.read_text(encoding="utf-8")

    def overlay_text(self, rel: str) -> str | None:
        return self._read_layer(self.overlay_dir, editable_path(rel))

    def read(self, rel: str) -> dict[str, Any]:
        """Effective text of a file (overlay wins, else pack) and the hash of the *overlay* file."""
        rel = editable_path(rel)
        overlay = self._read_layer(self.overlay_dir, rel)
        pack = self._read_layer(self.pack_dir, rel)
        text = overlay if overlay is not None else pack
        if text is None:
            raise WorkspaceError(f"'{rel}' does not exist")
        return {
            "path": rel,
            "content": text,
            "source": "overlay" if overlay is not None else "pack",
            "base_hash": content_hash(overlay),
        }

    # -- draft files ----------------------------------------------------------------------------------------
    def check_content(self, rel: str, content: str) -> None:
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise WorkspaceError(f"'{rel}' is larger than {MAX_FILE_BYTES // 1024} KiB")
        if "\x00" in content:
            raise WorkspaceError("binary content is not allowed")

    def with_file(
        self, files: dict[str, Any], rel: str, content: str | None, base_hash: str | None
    ) -> dict[str, Any]:
        """``files`` plus a change to ``rel``. A new draft file needs the hash the editor started from."""
        self.require_overlay()
        rel = editable_path(rel)
        if content is not None:
            self.check_content(rel, content)
        new = dict(files)
        if rel in new:
            base = new[rel]["base_hash"]
        else:
            current = content_hash(self.overlay_text(rel))
            if base_hash is not None and base_hash != current:
                raise Conflict(f"'{rel}' changed on disk since you opened it; reload it")
            base = current
        new[rel] = {"content": content, "base_hash": base}
        return new

    def conflicts(self, files: dict[str, Any]) -> list[str]:
        return [
            rel
            for rel, ch in sorted(files.items())
            if content_hash(self.overlay_text(rel)) != ch["base_hash"]
        ]

    # -- validation -----------------------------------------------------------------------------------------
    @contextlib.contextmanager
    def materialize(self, files: dict[str, Any]) -> Iterator[Path]:
        """A temp copy of the overlay with the draft applied (the real overlay is never touched)."""
        overlay = self.require_overlay()
        tmp = Path(tempfile.mkdtemp(prefix="pkpipe-draft-"))
        try:
            root = tmp / "overlay"
            shutil.copytree(
                overlay,
                root,
                symlinks=False,  # a real copy: draft files below must never be written through a link
                ignore_dangling_symlinks=True,
                ignore=shutil.ignore_patterns(".*", "evals", "__pycache__"),
            )
            for rel, ch in files.items():
                target = root / editable_path(rel)
                if not target.resolve().is_relative_to(root.resolve()):
                    raise WorkspaceError(f"'{rel}' resolves outside the draft copy")
                if ch["content"] is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(ch["content"], encoding="utf-8")
            yield root
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def load_draft(self, files: dict[str, Any]) -> tuple[EffectiveConfig | None, ValidationResult]:
        """Effective config of pack + draft overlay, or None with the errors."""
        for rel, ch in files.items():
            if ch["content"] is not None and rel.endswith(".json"):
                try:
                    json.loads(ch["content"])
                except json.JSONDecodeError as exc:
                    return None, ValidationResult(
                        False, [f"{rel}: invalid JSON ({exc.msg} at line {exc.lineno})"], []
                    )
        with self.materialize(files) as root:
            try:
                cfg = load_effective(self.pack_dir, root)
            except ConfigError as exc:
                return None, ValidationResult(False, list(exc.errors), [])
            cfg.overlay_dir = self.overlay_dir  # evals datasets of the real overlay, not the temp copy
            return cfg, ValidationResult(True, [], list(cfg.warnings), cfg.version)

    def validate(self, files: dict[str, Any]) -> ValidationResult:
        errors = [f"{rel}: changed on disk since the draft started from it" for rel in self.conflicts(files)]
        _, result = self.load_draft(files)
        if errors:
            return ValidationResult(False, errors + result.errors, result.warnings, result.version)
        return result

    def current(self) -> ConfigSnapshot | None:
        """Snapshot of the published config, or None when it does not load (a first publish gates all)."""
        try:
            return load_effective(self.pack_dir, self.overlay_dir).snapshot()
        except ConfigError:
            return None

    def draft_snapshot(self, files: dict[str, Any]) -> ConfigSnapshot:
        cfg, result = self.load_draft(files)
        if cfg is None:
            raise WorkspaceError("; ".join(result.errors))
        return cfg.snapshot()

    # -- diff -----------------------------------------------------------------------------------------------
    def diff(self, files: dict[str, Any]) -> list[dict[str, str]]:
        out = []
        for rel, ch in sorted(files.items()):
            old = self.overlay_text(rel) or ""
            new = ch["content"] or ""
            if old == new and (ch["content"] is None) == (self.overlay_text(rel) is None):
                continue
            text = "".join(
                difflib.unified_diff(
                    old.splitlines(keepends=True),
                    new.splitlines(keepends=True),
                    f"a/{rel}",
                    f"b/{rel}" if ch["content"] is not None else "/dev/null",
                )
            )
            out.append({"path": rel, "diff": text})
        return out


def category_entry(category_id: str, label: str, subflow: str, para_path: str, triage_hint: str) -> dict:
    """A new ``categories.json`` entry without sessions (add them in the editor, then publish)."""
    return {
        "id": category_id,
        "label": label,
        "subflow": subflow,
        "para_path": para_path,
        "triage_hint": triage_hint,
        "sessions": [],
    }
