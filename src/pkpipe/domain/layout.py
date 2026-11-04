"""The vault layout (spec 020): every folder and file the engine names, in one place.

The defaults equal the constants the engine used before spec 020, so a pack without a ``layout`` block
behaves as before. A private overlay changes the layout (``layout.json`` for the flows and steps, the
``layout`` block of the vault policy for the obsidian-vault server); nothing else in ``src/pkpipe`` spells a
vault folder.

The model is pure: it only validates and formats relative vault paths. Whether a path is allowed for a tool
is the vault policy's business (:func:`allowed_by`, :func:`layout_errors`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pkpipe.domain.inbox_list import InboxListConfig

_PATH_FIELDS = (
    "inbox_dir",
    "inbox_list_file",
    "triage_file",
    "processing_list",
    "archive_dir",
    "inbox_archive_pattern",
    "agent_tasks_dir",
    "tool_candidates_file",
    "improvement_file",
    "proposals_dir",
    "quarantine_dir",
    "journal_pattern",
    "attachments_dir",
    "projects_dir",
    "areas_dir",
    "resources_dir",
    "context_review_dir",
)
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
JOURNAL_PLACEHOLDERS = ("yyyy", "MM", "dd", "yyyy-MM-dd")


def check_relative(value: str, *, what: str) -> tuple[str, ...]:
    """The segments of a vault relative path; raises ``ValueError`` for anything else (FR-008)."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must not be empty")
    if "\x00" in value or "\\" in value or ":" in value:
        raise ValueError(f"{what} '{value}' contains forbidden characters")
    if value.startswith(("/", "~")):
        raise ValueError(f"{what} '{value}' must be relative to the vault")
    parts = tuple(value.rstrip("/").split("/"))
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"{what} '{value}' must not contain '..', '.' or empty segments")
    if any(p.startswith(".") for p in parts):
        raise ValueError(f"{what} '{value}' must not use hidden folders")
    return parts


class InboxListSettings(BaseModel):
    """How the list file is read (data-model.md ``InboxListConfig``); the defaults fit the real INBOX."""

    model_config = ConfigDict(extra="forbid")

    bullet_markers: list[str] = Field(default_factory=lambda: ["-", "*"], min_length=1)
    header_end: str = r"^---\s*$"
    strip_params: list[str] = Field(default_factory=lambda: ["utm_*", "fbclid", "gclid", "igshid", "mc_*"])
    comments_ignored: bool = True

    @field_validator("header_end")
    @classmethod
    def _regex(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"header_end is not a valid pattern: {exc}") from None
        return value

    def to_config(self) -> InboxListConfig:
        return InboxListConfig.from_mapping(self.model_dump())


class VaultLayout(BaseModel):
    """Places of the vault, all relative to the vault root (data-model.md ``VaultLayout``)."""

    model_config = ConfigDict(extra="forbid")

    inbox_dir: str = "0-INBOX"
    #: A single list file whose bullet entries are the triage items; ``None`` = one note per item.
    inbox_list_file: str | None = None
    triage_file: str = "0-INBOX/Inbox-Triage.md"
    processing_list: str = "0-INBOX/Manual-Resource-Processing-List.md"
    #: Where a processed INBOX note moves to.
    archive_dir: str = "4-Archive/Inbox"
    #: Dated archive file of the entries removed from the list (placeholders as in ``journal_pattern``).
    inbox_archive_pattern: str = "4-Archive/Inbox/Inbox-Archive-{yyyy-MM-dd}.md"
    agent_tasks_dir: str = "2-Areas/Agent-Tasks"
    tool_candidates_file: str = "2-Areas/System/System-Tool-Candidates.md"
    improvement_file: str = "2-Areas/Self-Improvement/Improvement-Backlog.md"
    proposals_dir: str = "2-Areas/Self-Improvement/System-Changes"
    #: Only ``quarantine_note`` writes here and only the owner's release moves a note out.
    quarantine_dir: str = "0-INBOX/_quarantine"
    journal_pattern: str = "2-Areas/Journal/{yyyy-MM-dd}.md"
    attachments_dir: str = "0-INBOX/attachments"
    projects_dir: str = "1-Projects"
    areas_dir: str = "2-Areas"
    resources_dir: str = "3-Resources"
    context_review_dir: str = "2-Areas/Context-Review"
    #: Parser settings of the list file (``inbox_list_file``).
    inbox_list: InboxListSettings = Field(default_factory=InboxListSettings)

    @field_validator(*_PATH_FIELDS, mode="before")
    @classmethod
    def _relative(cls, value: Any, info: Any) -> Any:
        if value is None and info.field_name == "inbox_list_file":
            return None
        check_relative(value, what=info.field_name)
        return value.rstrip("/") if isinstance(value, str) else value

    @field_validator("journal_pattern", "inbox_archive_pattern")
    @classmethod
    def _placeholders(cls, value: str, info: Any) -> str:
        for name in _PLACEHOLDER.findall(value):
            if name not in JOURNAL_PLACEHOLDERS:
                raise ValueError(
                    f"{info.field_name}: unknown placeholder '{{{name}}}' "
                    f"(use {', '.join('{' + p + '}' for p in JOURNAL_PLACEHOLDERS)})"
                )
        if not value.endswith(".md"):
            raise ValueError(f"{info.field_name} must name a .md file")
        return value

    @field_validator(
        "inbox_list_file", "triage_file", "processing_list", "tool_candidates_file", "improvement_file"
    )
    @classmethod
    def _markdown_file(cls, value: str | None, info: Any) -> str | None:
        if value is not None and not value.endswith(".md"):
            raise ValueError(f"{info.field_name} must name a .md file")
        return value

    # -- derived places --------------------------------------------------------------------------------
    @property
    def archive_root(self) -> str:
        """Top level folder of the archive (the folder the archive notes live below)."""
        return check_relative(self.archive_dir, what="archive_dir")[0]

    @property
    def managed_names(self) -> tuple[str, ...]:
        """File names in the INBOX folder that are never items (triage file, processing list, list file)."""
        names = [PurePosixPath(self.triage_file).name, PurePosixPath(self.processing_list).name]
        if self.inbox_list_file:
            names.append(PurePosixPath(self.inbox_list_file).name)
        return tuple(names)

    def format_pattern(self, pattern: str, day: date) -> str:
        """Fill ``{yyyy}``, ``{MM}``, ``{dd}`` and ``{yyyy-MM-dd}`` of a path pattern."""
        values = {
            "yyyy": f"{day.year:04d}",
            "MM": f"{day.month:02d}",
            "dd": f"{day.day:02d}",
            "yyyy-MM-dd": day.isoformat(),
        }
        return _PLACEHOLDER.sub(lambda m: values[m.group(1)], pattern)

    def journal_path(self, day: date) -> str:
        return self.format_pattern(self.journal_pattern, day)

    def inbox_archive_path(self, day: date) -> str:
        return self.format_pattern(self.inbox_archive_pattern, day)

    def default_allow(self) -> dict[str, list[str]]:
        """Folder allowlists per vault tool that follow this layout (the policy defaults)."""
        para = [self.inbox_dir, self.projects_dir, self.areas_dir, self.resources_dir, self.archive_root]
        write = [self.inbox_dir, self.projects_dir, self.areas_dir, self.resources_dir]
        return {
            "list_folder": list(para),
            "read_note": list(para),
            "write_note": list(write),
            "append_note": list(write),
            "move_note": list(para),
            "read_triage": [self.inbox_dir],
            "update_triage": [self.inbox_dir],
            "upsert_processing_list": [self.inbox_dir],
            "read_notes": list(para),
            "read_improvements": [self.areas_dir],
            "update_improvements": [self.areas_dir],
            "read_tool_candidates": [self.areas_dir],
            "update_tool_candidates": [self.areas_dir],
            "quarantine_note": [self.inbox_dir],
            "inbox_remove_entries": [self.inbox_dir, self.archive_root],
        }

    def tool_paths(self) -> list[tuple[str, str, str]]:
        """``(tool, field, path)`` for every path a vault tool reads or writes (FR-008). Pattern fields are
        checked through their folder (the part before the first placeholder segment). Destinations of the
        generic note tools (``archive_dir``, ``agent_tasks_dir``, the PARA folders) are not listed: an owner
        may narrow ``write_note`` on purpose; the resolver refuses other paths at run time."""
        out = [
            ("read_triage", "triage_file", self.triage_file),
            ("update_triage", "triage_file", self.triage_file),
            ("upsert_processing_list", "processing_list", self.processing_list),
            ("read_improvements", "improvement_file", self.improvement_file),
            ("update_improvements", "improvement_file", self.improvement_file),
            ("read_tool_candidates", "tool_candidates_file", self.tool_candidates_file),
            ("update_tool_candidates", "tool_candidates_file", self.tool_candidates_file),
            ("quarantine_note", "quarantine_dir", self.quarantine_dir),
            ("read_notes", "journal_pattern", self.journal_pattern),
        ]
        if self.inbox_list_file:
            out += [
                ("read_note", "inbox_list_file", self.inbox_list_file),
                ("inbox_remove_entries", "inbox_list_file", self.inbox_list_file),
                ("inbox_remove_entries", "inbox_archive_pattern", self.inbox_archive_pattern),
            ]
        return out


DEFAULT_LAYOUT = VaultLayout()


def allowed_by(path: str, prefixes: Sequence[str]) -> bool:
    """Whether ``path`` lies at or below one of the folder prefixes (case sensitive, segment wise)."""
    parts = tuple(path.split("/"))
    for prefix in prefixes:
        pre = tuple(prefix.rstrip("/").split("/"))
        if parts[: len(pre)] == pre:
            return True
    return False


def _static_part(pattern: str) -> str:
    """The leading segments of a path pattern that contain no placeholder."""
    keep: list[str] = []
    for seg in pattern.split("/"):
        if "{" in seg:
            break
        keep.append(seg)
    return "/".join(keep) or pattern


def layout_errors(layout: VaultLayout, allow: Mapping[str, Sequence[str]]) -> list[str]:
    """Every layout path that the allowlist of the tool using it does not cover; each error names the path
    and the tool (spec 020 US2 scenario 3, FR-008)."""
    errors: list[str] = []
    for tool, field, path in layout.tool_paths():
        checked = _static_part(path)
        if not allowed_by(checked, allow.get(tool, [])):
            errors.append(f"layout.{field} '{path}' is outside the allowlist of tool '{tool}'")
    return errors


def layout_from(data: Mapping[str, Any] | None) -> VaultLayout:
    """The layout of an effective config (``layout.json`` merged over the defaults)."""
    return VaultLayout(**dict(data or {}))


__all__ = [
    "DEFAULT_LAYOUT",
    "JOURNAL_PLACEHOLDERS",
    "InboxListSettings",
    "VaultLayout",
    "allowed_by",
    "check_relative",
    "layout_errors",
    "layout_from",
]
