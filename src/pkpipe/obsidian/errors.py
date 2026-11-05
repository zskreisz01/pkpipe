"""Errors raised by the vault layer; the MCP server turns them into tool errors."""

from __future__ import annotations


class VaultError(Exception):
    """Base class; the message is safe to show to the calling agent."""


class PathRejected(VaultError):
    """A path violates the allowlist or the traversal rules (FR-002)."""


class NoteNotFound(VaultError):
    pass


class NoteExists(VaultError):
    pass


class WriteConflict(VaultError):
    """The file kept changing underneath the writer; the caller may retry."""


class InvalidRows(VaultError):
    """Triage / processing-list input failed validation; nothing was written."""


class ImageRejected(VaultError):
    """``read_image`` refused a name or a file (spec 021 FR-004). ``code`` is stable: ``not_found``,
    ``bad_name``, ``bad_type`` or ``too_large``; the message never names the file."""

    CODES = ("not_found", "bad_name", "bad_type", "too_large")

    def __init__(self, code: str, detail: str = ""):
        assert code in self.CODES
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)
