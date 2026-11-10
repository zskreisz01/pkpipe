"""Path allowlist and safe path resolver (spec 004 FR-002, T001, T006).

Every tool resolves its paths through :meth:`VaultPolicy.resolve`. A path is accepted only when it is a
relative, normalised, non-hidden path whose folder prefix is on the allowlist of *that tool* and whose
symlink-resolved location is still inside the vault and inside an allowed folder.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from pkpipe.domain.layout import LEGACY_LAYOUT_KEYS, VaultLayout, layout_errors

from .errors import PathRejected

TOOLS = (
    "list_folder",
    "read_note",
    "write_note",
    "append_note",
    "move_note",
    "read_triage",
    "update_triage",
    "upsert_processing_list",
    "read_notes",
    "read_improvements",
    "update_improvements",
    "read_tool_candidates",
    "update_tool_candidates",
    "quarantine_note",
    "inbox_remove_entries",
    "read_processing_list",
    "read_in_progress",
    "inbox_in_progress",
    "write_pipeline",
    "write_results_index",
    "import_audio",
    "read_image",
    "unreviewed_notes",
    "write_journal_todos",
    "failed_items",
)
#: ``read_image`` size limit (spec 021 FR-004); a policy file may lower or raise it.
DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024
#: Folder allowlists of the default layout (a layout derives its own: ``VaultLayout.default_allow``).
DEFAULT_ALLOW: dict[str, list[str]] = VaultLayout().default_allow()
CONFLICT_RE = re.compile(r"\([^)]*conflict[^)]*\)|\.sync-conflict-", re.IGNORECASE)


def is_conflict_name(name: str) -> bool:
    """Obsidian Sync / Syncthing style conflict copies such as ``note (conflict).md``."""
    return CONFLICT_RE.search(name) is not None


class VaultPolicy(BaseModel):
    vault_dir: Path
    allow: dict[str, list[str]] = Field(default_factory=dict)
    #: Every folder and file the vault tools name (spec 020); defaults = the pre-020 constants.
    layout: VaultLayout = Field(default_factory=VaultLayout)
    #: Spec 023: where the triage table lives (mirrors ``triage.json`` ``surface``; ``pkpipe validate`` checks
    #: both agree): ``vault`` = the triage file, ``web`` = ``layout.triage_data_file``.
    triage_surface: Literal["vault", "web"] = "vault"
    #: Spec 023: the address of the web UI, shown on the generated triage page (``web`` surface); mirrors
    #: ``ui.json`` ``public_url``.
    ui_public_url: str | None = None
    #: ``read_image`` refuses a larger file (``too_large``)
    max_image_bytes: int = Field(default=DEFAULT_MAX_IMAGE_BYTES, gt=0)
    #: Spec 032: absolute folders ``import_audio`` may copy a podcast from (the gateway's audio folder).
    #: Empty = the tool refuses every copy.
    audio_import_dirs: list[Path] = Field(default_factory=list)
    #: ``import_audio`` refuses a larger file
    max_audio_bytes: int = Field(default=200 * 1024 * 1024, gt=0)

    @model_validator(mode="before")
    @classmethod
    def _legacy_keys(cls, data: Any) -> Any:
        """``triage_file`` & co. at the top level of a policy file are the pre-020 spelling of ``layout``."""
        if not isinstance(data, dict) or not any(k in data for k in LEGACY_LAYOUT_KEYS):
            return data
        data = dict(data)
        layout = data.get("layout")
        layout = layout.model_dump() if isinstance(layout, VaultLayout) else dict(layout or {})
        for key in LEGACY_LAYOUT_KEYS:
            if key in data:
                layout.setdefault(key, data.pop(key))
        data["layout"] = layout
        return data

    @model_validator(mode="after")
    def _derive_and_check_layout(self) -> VaultPolicy:
        if "allow" not in self.model_fields_set:
            self.allow = self.layout.default_allow()
        errors = layout_errors(self.layout, {t: v for t, v in self.allow.items()})
        # a tool without any allowlist entry is not configured (direct construction); skip its paths
        errors = [e for e in errors if any(f"'{t}'" in e for t in self.allow)]
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @property
    def triage_file(self) -> str:
        return self.layout.triage_file

    @property
    def processing_list(self) -> str:
        return self.layout.processing_list

    @property
    def improvement_file(self) -> str:
        return self.layout.improvement_file

    @property
    def tool_candidates_file(self) -> str:
        return self.layout.tool_candidates_file

    @property
    def quarantine_dir(self) -> str:
        return self.layout.quarantine_dir

    @field_validator("allow")
    @classmethod
    def _check_allow(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for tool, prefixes in value.items():
            if tool not in TOOLS:
                raise ValueError(f"unknown tool in allowlist: {tool}")
            for prefix in prefixes:
                try:
                    _lexical_parts(prefix, what="allowlist prefix")
                except PathRejected as exc:
                    raise ValueError(str(exc)) from None
        return value

    @classmethod
    def load(cls, config_path: Path | None, vault_dir: Path | None = None) -> VaultPolicy:
        data: dict = json.loads(config_path.read_text(encoding="utf-8")) if config_path else {}
        if vault_dir is not None:
            data["vault_dir"] = str(vault_dir)
        if "vault_dir" not in data:
            raise ValueError("vault_dir is required (config file or --vault)")
        data = _merge_allow(data)
        return cls(**data)

    @property
    def root(self) -> Path:
        return self.vault_dir.resolve()

    @property
    def managed_files(self) -> tuple[str, ...]:
        files = [
            self.triage_file,
            self.layout.triage_data,
            self.layout.in_progress,
            self.layout.pipeline,
            self.processing_list,
            self.improvement_file,
            self.tool_candidates_file,
        ]
        if self.layout.inbox_list_file:
            files.append(self.layout.inbox_list_file)
        return tuple(files)

    def prefixes(self, tool: str) -> list[tuple[str, ...]]:
        if tool not in TOOLS:
            raise PathRejected(f"unknown tool: {tool}")
        return [_lexical_parts(p, what="allowlist prefix") for p in self.allow.get(tool, [])]

    def resolve(
        self, rel: str, tool: str, *, kind: Literal["file", "folder"] = "file", must_exist: bool = False
    ) -> Path:
        """Validate ``rel`` for ``tool`` and return the absolute path inside the vault."""
        parts = _lexical_parts(rel, what="path")
        if kind == "file" and not parts[-1].endswith(".md"):
            raise PathRejected("only .md notes are accessible")
        prefixes = self.prefixes(tool)
        if not any(parts[: len(p)] == p and (len(parts) > len(p) or kind == "folder") for p in prefixes):
            raise PathRejected(f"path is outside the allowlist of {tool}")
        root = self.root
        candidate = (root / Path(*parts)).resolve()
        try:
            inside = candidate.relative_to(root).parts
        except ValueError:
            raise PathRejected("path escapes the vault") from None
        if any(x.startswith(".") for x in inside):
            raise PathRejected("hidden folders are not accessible")
        if not any(inside[: len(p)] == p for p in prefixes):
            raise PathRejected(f"path resolves outside the allowlist of {tool}")
        if must_exist and not candidate.exists():
            raise PathRejected("path does not exist")
        return candidate

    def is_managed(self, rel: str) -> bool:
        norm = PurePosixPath(rel).as_posix()
        return norm in self.managed_files

    def in_quarantine(self, absolute: Path) -> bool:
        """Whether a resolved path lies in the quarantine folder (case-folded, for APFS)."""
        q = _lexical_parts(self.quarantine_dir, what="quarantine_dir")
        try:
            parts = absolute.resolve().relative_to(self.root).parts
        except ValueError:
            return False
        fold = [p.casefold() for p in parts[: len(q)]]
        return fold == [p.casefold() for p in q]

    def rel(self, absolute: Path) -> str:
        return absolute.resolve().relative_to(self.root).as_posix()


def _merge_allow(data: dict) -> dict:
    """Policy file data with the layout's default allowlists under the explicit ones (explicit wins)."""
    layout = data.get("layout") or {}
    merged = VaultLayout(**layout).default_allow()
    merged.update({k: list(v) for k, v in (data.get("allow") or {}).items()})
    return {**data, "allow": merged}


def _lexical_parts(value: str, *, what: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip():
        raise PathRejected(f"{what} must not be empty")
    if "\x00" in value or "\\" in value or ":" in value:
        raise PathRejected(f"{what} contains forbidden characters")
    if value.startswith(("/", "~")):
        raise PathRejected(f"{what} must be relative to the vault")
    parts = tuple(value.split("/"))
    if value.endswith("/"):
        parts = parts[:-1]
    if any(p in ("", ".", "..") for p in parts):
        raise PathRejected(f"{what} must not contain '..', '.' or empty segments")
    if any(p.startswith(".") for p in parts):
        raise PathRejected("hidden folders are not accessible")
    return parts
