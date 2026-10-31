"""Path allowlist and safe path resolver (spec 004 FR-002, T001, T006).

Every tool resolves its paths through :meth:`VaultPolicy.resolve`. A path is accepted only when it is a
relative, normalised, non-hidden path whose folder prefix is on the allowlist of *that tool* and whose
symlink-resolved location is still inside the vault and inside an allowed folder.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, field_validator

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
)
PARA = ("0-INBOX", "1-Projects", "2-Areas", "3-Resources", "4-Archive")
DEFAULT_ALLOW: dict[str, list[str]] = {
    "list_folder": list(PARA),
    "read_note": list(PARA),
    "write_note": ["0-INBOX", "1-Projects", "2-Areas", "3-Resources"],
    "append_note": ["0-INBOX", "1-Projects", "2-Areas", "3-Resources"],
    "move_note": list(PARA),
    "read_triage": ["0-INBOX"],
    "update_triage": ["0-INBOX"],
    "upsert_processing_list": ["0-INBOX"],
}
CONFLICT_RE = re.compile(r"\([^)]*conflict[^)]*\)|\.sync-conflict-", re.IGNORECASE)


def is_conflict_name(name: str) -> bool:
    """Obsidian Sync / Syncthing style conflict copies such as ``note (conflict).md``."""
    return CONFLICT_RE.search(name) is not None


class VaultPolicy(BaseModel):
    vault_dir: Path
    allow: dict[str, list[str]] = Field(
        default_factory=lambda: {k: list(v) for k, v in DEFAULT_ALLOW.items()}
    )
    triage_file: str = "0-INBOX/Inbox-Triage.md"
    processing_list: str = "0-INBOX/Manual-Resource-Processing-List.md"

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
        policy = cls(**data)
        merged = {k: list(v) for k, v in DEFAULT_ALLOW.items()}
        merged.update(policy.allow)
        policy.allow = merged
        return policy

    @property
    def root(self) -> Path:
        return self.vault_dir.resolve()

    @property
    def managed_files(self) -> tuple[str, str]:
        return (self.triage_file, self.processing_list)

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

    def rel(self, absolute: Path) -> str:
        return absolute.resolve().relative_to(self.root).as_posix()


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
