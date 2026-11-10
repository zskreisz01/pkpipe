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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pkpipe.domain.inbox_list import InboxListConfig

_PATH_FIELDS = (
    "inbox_dir",
    "inbox_list_file",
    "inbox_in_progress_file",
    "triage_file",
    "triage_data_file",
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
    #: Spec 031: the machine-owned table of the items worked on but not closed. ``None``:
    #: ``<inbox_dir>/INBOX-InProgress.md``.
    inbox_in_progress_file: str | None = None
    triage_file: str = "0-INBOX/Inbox-Triage.md"
    #: Spec 023: where the machine-owned triage table lives when ``triage.surface`` is ``web`` (the triage
    #: file is then a generated summary page without a table). ``None``:
    #: ``<inbox_dir>/_pkpipe/triage-data.md``.
    triage_data_file: str | None = None
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
        if value is None and info.field_name in (
            "inbox_list_file",
            "inbox_in_progress_file",
            "triage_data_file",
        ):
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
        "inbox_list_file",
        "inbox_in_progress_file",
        "triage_file",
        "triage_data_file",
        "processing_list",
        "tool_candidates_file",
        "improvement_file",
    )
    @classmethod
    def _markdown_file(cls, value: str | None, info: Any) -> str | None:
        if value is not None and not value.endswith(".md"):
            raise ValueError(f"{info.field_name} must name a .md file")
        return value

    @model_validator(mode="after")
    def _no_overlapping_files(self) -> VaultLayout:
        """The files the tools maintain are distinct, and the list file and the archive are not among them
        (a layout where the archive is the triage file would let ``inbox_remove_entries`` write it)."""
        managed = {
            "triage_file": self.triage_file,
            "triage_data_file": self.triage_data,
            "in_progress_file": self.in_progress,
            "processing_list": self.processing_list,
            "improvement_file": self.improvement_file,
            "tool_candidates_file": self.tool_candidates_file,
        }
        if self.inbox_list_file:
            managed["inbox_list_file"] = self.inbox_list_file
        seen: dict[str, str] = {}
        for field, path in managed.items():
            key = path.casefold()
            if key in seen:
                raise ValueError(f"layout.{field} and layout.{seen[key]} name the same file '{path}'")
            seen[key] = field
        quarantine = self.quarantine_dir.casefold()
        for field, path in managed.items():
            if path.casefold() == quarantine or path.casefold().startswith(quarantine + "/"):
                raise ValueError(f"layout.{field} '{path}' lies in the quarantine folder")
        for field, path in (("inbox_archive_pattern", self.inbox_archive_pattern),):
            if "{" not in path and path.casefold() in seen:
                raise ValueError(
                    f"layout.{field} '{path}' is the managed file layout.{seen[path.casefold()]}"
                )
        # The archive holds the only copy of removed entries: it lives outside every folder the generic
        # note tools may write (or triage) in, so nothing else can overwrite, move into or triage it.
        folder = "/".join(self.inbox_archive_pattern.split("/")[:-1])
        writable = [self.inbox_dir, self.projects_dir, self.areas_dir, self.resources_dir]
        static = folder.split("{")[0].rstrip("/")
        if static and allowed_by(static, writable):
            raise ValueError(
                f"layout.inbox_archive_pattern '{self.inbox_archive_pattern}' lies in a writable or INBOX "
                "folder; put the archive below archive_dir"
            )
        return self

    # -- derived places --------------------------------------------------------------------------------
    @property
    def triage_data(self) -> str:
        """The data file of the ``web`` surface (spec 023): the configured one, else below the INBOX."""
        return self.triage_data_file or f"{self.inbox_dir}/_pkpipe/triage-data.md"

    @property
    def in_progress(self) -> str:
        """The unfinished-items table (spec 031): the configured file, else below the INBOX folder."""
        return self.inbox_in_progress_file or f"{self.inbox_dir}/INBOX-InProgress.md"

    @property
    def archive_root(self) -> str:
        """Top level folder of the archive (the folder the archive notes live below)."""
        return check_relative(self.archive_dir, what="archive_dir")[0]

    @property
    def managed_names(self) -> tuple[str, ...]:
        """File names in the INBOX folder that are never items (triage file, processing list, list file)."""
        names = [
            PurePosixPath(self.triage_file).name,
            PurePosixPath(self.triage_data).name,
            PurePosixPath(self.in_progress).name,
            PurePosixPath(self.processing_list).name,
        ]
        if self.inbox_list_file:
            names.append(PurePosixPath(self.inbox_list_file).name)
        return tuple(names)

    def snapshot_roots(self) -> list[str]:
        """The vault folders the pipeline writes to, one per top level: the scope a vault snapshot (spec 019)
        covers, derived from the layout instead of its own list (FR-009)."""
        roots = [self.inbox_dir, self.projects_dir, self.areas_dir, self.resources_dir, self.archive_root]
        paths = [
            self.triage_file,
            self.triage_data,
            self.in_progress,
            self.processing_list,
            self.improvement_file,
            self.tool_candidates_file,
        ]
        extra = [self.inbox_archive_pattern, *([self.inbox_list_file] if self.inbox_list_file else [])]
        roots += [
            p.split("/")[0]
            for p in (
                *paths,
                *extra,
                self.quarantine_dir,
                self.agent_tasks_dir,
                self.proposals_dir,
                self.context_review_dir,
            )  # attachments_dir is read only (spec 021 FR-010): never part of a snapshot
        ]
        top = {r.split("/")[0] for r in roots}
        return sorted(top)

    def format_pattern(self, pattern: str, day: date) -> str:
        """Fill ``{yyyy}``, ``{MM}``, ``{dd}`` and ``{yyyy-MM-dd}`` of a path pattern."""
        values = {
            "yyyy": f"{day.year:04d}",
            "MM": f"{day.month:02d}",
            "dd": f"{day.day:02d}",
            "yyyy-MM-dd": day.isoformat(),
        }
        return _PLACEHOLDER.sub(lambda m: values[m.group(1)], pattern)

    def expand(self, path: str) -> str:
        """A configured path with ``{inbox_dir}``, ``{projects_dir}``, ``{areas_dir}``, ``{resources_dir}``
        and ``{archive_dir}`` filled from this layout (a category ``para_path`` follows its folders)."""
        values = {
            "inbox_dir": self.inbox_dir,
            "projects_dir": self.projects_dir,
            "areas_dir": self.areas_dir,
            "resources_dir": self.resources_dir,
            "archive_dir": self.archive_dir,
        }
        return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), path)

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
            "read_processing_list": [self.inbox_dir],
            "read_in_progress": [self.inbox_dir],
            "inbox_in_progress": [self.inbox_dir, self.archive_root],
            "read_notes": list(para),
            "read_improvements": [self.areas_dir],
            "update_improvements": [self.areas_dir],
            "read_tool_candidates": [self.areas_dir],
            "update_tool_candidates": [self.areas_dir],
            "quarantine_note": [self.inbox_dir],
            "inbox_remove_entries": [self.inbox_dir, self.archive_root],
            "read_image": [self.attachments_dir],  # its own entry: no other tool reads or writes images
            # spec 024: counts of notes the owner has not marked reviewed (no text is returned)
            "unreviewed_notes": [self.projects_dir, self.areas_dir, self.resources_dir],
            # ``write_journal_todos`` has no default: the owner grants the journal folder on purpose
        }

    def tool_paths(self) -> list[tuple[str, str, str]]:
        """``(tool, field, path)`` for every path a vault tool reads or writes (FR-008). Pattern fields are
        checked through their folder (the part before the first placeholder segment). Destinations of the
        generic note tools (``archive_dir``, ``agent_tasks_dir``, the PARA folders) are not listed: an owner
        may narrow ``write_note`` on purpose; the resolver refuses other paths at run time."""
        out = [
            ("read_triage", "triage_file", self.triage_file),
            ("update_triage", "triage_file", self.triage_file),
            ("read_triage", "triage_data_file", self.triage_data),
            ("update_triage", "triage_data_file", self.triage_data),
            ("upsert_processing_list", "processing_list", self.processing_list),
            ("read_processing_list", "processing_list", self.processing_list),
            ("read_in_progress", "in_progress_file", self.in_progress),
            ("inbox_in_progress", "in_progress_file", self.in_progress),
            ("read_improvements", "improvement_file", self.improvement_file),
            ("update_improvements", "improvement_file", self.improvement_file),
            ("read_tool_candidates", "tool_candidates_file", self.tool_candidates_file),
            ("update_tool_candidates", "tool_candidates_file", self.tool_candidates_file),
            ("quarantine_note", "quarantine_dir", self.quarantine_dir),
            ("read_note", "journal_pattern", self.journal_pattern),
            ("read_notes", "journal_pattern", self.journal_pattern),
            ("read_image", "attachments_dir", self.attachments_dir),
        ]
        if self.inbox_list_file:
            out += [
                ("read_note", "inbox_list_file", self.inbox_list_file),
                ("inbox_remove_entries", "inbox_list_file", self.inbox_list_file),
                ("inbox_remove_entries", "inbox_archive_pattern", self.inbox_archive_pattern),
                ("inbox_in_progress", "inbox_list_file", self.inbox_list_file),
                ("inbox_in_progress", "inbox_archive_pattern", self.inbox_archive_pattern),
            ]
        return out


def allowed_by(path: str, prefixes: Sequence[str]) -> bool:
    """Whether ``path`` lies at or below one of the folder prefixes (case sensitive, segment wise)."""
    parts = tuple(path.split("/"))
    for prefix in prefixes:
        pre = tuple(prefix.rstrip("/").split("/"))
        if parts[: len(pre)] == pre:
            return True
    return False


DEFAULT_LAYOUT = VaultLayout()


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
    # spec 024: the todo block is written only where the owner granted it; no grant, no check
    if allow.get("write_journal_todos") and not allowed_by(
        _static_part(layout.journal_pattern), allow["write_journal_todos"]
    ):
        errors.append(
            f"layout.journal_pattern '{layout.journal_pattern}' is outside the allowlist of tool "
            "'write_journal_todos'"
        )
    return errors


#: Fields whose place follows from the PARA structure; the journal pattern is deliberately free (an owner
#: grants its folder in the vault policy ``allow``), so it is not part of the structure check.
STRUCTURAL_FIELDS = (
    "triage_file",
    "triage_data_file",
    "processing_list",
    "improvement_file",
    "tool_candidates_file",
    "quarantine_dir",
    "inbox_list_file",
    "inbox_archive_pattern",
)


#: Folders flows write notes into; they must lie in the folders the write tools default to.
#: ``attachments_dir`` is not one of them: the engine only reads it (``read_image``, spec 021 FR-010).
WRITE_DESTINATION_FIELDS = ("agent_tasks_dir", "proposals_dir", "context_review_dir")


def layout_structure_errors(layout: VaultLayout) -> list[str]:
    """Paths that lie outside the folders the vault tool defaults derive from this layout (``inbox_dir``,
    the PARA roots, the archive root). Catches a half-moved layout, for example a new ``inbox_dir`` with the
    triage file still in the old folder, when the config is validated or published (FR-008)."""
    allow = layout.default_allow()
    errors = layout_errors(layout, allow)
    errors = [e for e in errors if any(e.startswith(f"layout.{f} ") for f in STRUCTURAL_FIELDS)]
    for field in WRITE_DESTINATION_FIELDS:  # flows write notes below these (write_note / append_note)
        path = getattr(layout, field)
        if not allowed_by(path, allow["write_note"]):
            errors.append(f"layout.{field} '{path}' is outside the allowlist of tool 'write_note'")
    return errors


#: Top level keys of an older vault policy file that are layout fields (pre spec 020 spelling).
LEGACY_LAYOUT_KEYS = (
    "triage_file",
    "processing_list",
    "improvement_file",
    "tool_candidates_file",
    "quarantine_dir",
)


def policy_file_errors(
    layout: VaultLayout, policy: Mapping[str, Any], surface: str = "vault", public_url: str | None = None
) -> list[str]:
    """Errors of an overlay layout against the vault policy file the vault server runs with (FR-008).

    The layout of the policy and the overlay ``layout.json`` must be the same, and every layout path must be
    allowed for the tool using it by the allowlists the policy ends up with (explicit ``allow`` entries over
    the defaults derived from the policy layout). Each error names the path and the tool."""
    data = dict(policy)
    raw = dict(data.get("layout") or {})
    for key in LEGACY_LAYOUT_KEYS:
        if key in data:
            raw.setdefault(key, data[key])
    try:
        policy_layout = VaultLayout(**raw)
    except ValueError as exc:
        return [f"vault policy layout is invalid: {exc}"]
    errors = [
        f"layout.{name} '{getattr(layout, name)}' differs from the vault policy layout "
        f"'{getattr(policy_layout, name)}'"
        for name in VaultLayout.model_fields
        if getattr(layout, name) != getattr(policy_layout, name)
    ]
    allow = policy_layout.default_allow()
    allow.update({k: list(v) for k, v in (data.get("allow") or {}).items()})
    errors += [f"vault policy: {e}" for e in layout_errors(layout, allow)]
    policy_surface = data.get("triage_surface", "vault")
    if policy_surface != surface:
        errors.append(
            f"triage.json surface '{surface}' differs from the vault policy triage_surface "
            f"'{policy_surface}': the vault server would read the table from the wrong file"
        )
    if surface == "web" and data.get("ui_public_url") != public_url:
        errors.append(
            f"ui.json public_url '{public_url}' differs from the vault policy ui_public_url "
            f"'{data.get('ui_public_url')}': the generated triage page would link to the wrong address"
        )
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
    "layout_structure_errors",
    "layout_from",
    "policy_file_errors",
    "LEGACY_LAYOUT_KEYS",
]
