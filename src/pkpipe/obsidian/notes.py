"""Notes with YAML front matter (spec 004 FR-003, T003)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import yaml

from .errors import VaultError

_FM_RE = re.compile(r"\A(---[ \t]*\r?\n)(.*?)(\r?\n---[ \t]*(?:\r?\n|\Z))", re.DOTALL)


@dataclass
class Note:
    raw_header: str  # exact front matter block including fences, "" when there is none
    body: str
    front_matter: dict[str, Any]
    front_matter_error: str | None = None


def parse_note(text: str) -> Note:
    match = _FM_RE.match(text)
    if not match:
        return Note("", text, {})
    header = match.group(0)
    body = text[match.end() :]
    try:
        data = yaml.safe_load(match.group(2)) or {}
        if not isinstance(data, dict):
            return Note(header, body, {}, "front matter is not a mapping")
    except yaml.YAMLError as exc:
        return Note(header, body, {}, f"invalid YAML: {str(exc).splitlines()[0]}")
    return Note(header, body, data)


def dump_front_matter(data: dict[str, Any]) -> str:
    if not data:
        return ""
    try:
        dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    except yaml.YAMLError as exc:
        raise VaultError(f"front matter is not serialisable: {exc}") from None
    return f"---\n{dumped}---\n"


def render_note(body: str, front_matter: dict[str, Any] | None, existing: Note | None) -> str:
    """Front matter ``None`` keeps the existing block byte for byte; a dict replaces it."""
    if front_matter is None:
        header = existing.raw_header if existing else ""
    else:
        header = dump_front_matter(front_matter)
    if body.startswith("---") and not header and _FM_RE.match(body):
        raise VaultError("body must not start with a front matter block; pass front_matter instead")
    return header + body


def append_text(existing_text: str, addition: str) -> str:
    """Append to the body; the front matter block stays byte-identical."""
    sep = "" if not existing_text or existing_text.endswith("\n") else "\n"
    return existing_text + sep + addition


def jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))
