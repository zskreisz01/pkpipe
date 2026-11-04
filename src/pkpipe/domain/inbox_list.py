"""The list-style INBOX (spec 020): one Markdown file whose bullet entries are the triage items.

Shape of the real list (data-model.md)::

    ---                      <- optional YAML front matter
    tags: [inbox]
    ---
    # Inbox                  <- title and intro text: the header
    Things to look at later.
    ---                      <- ``header_end``: entries follow
    - https://example.org/a?utm_source=x       link
    - ![[IMG_0001.jpg]]                         image embed
    - Some title                                text + link: the URL is on the next, unindented line
    https://example.org/b
    - a plain thought                           text
    <!-- owner note: kept, never an entry -->

Everything here is pure (strings in, values out) and decided by code (constitution V): kinds, the item
identity (hash of the normalised resource: tracking parameters never change it) and the removal of consumed
entries, which matches entries by their raw text and never touches another byte of the file.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

from pkpipe.domain.triage import item_id_for


class EntryKind(StrEnum):
    LINK = "link"
    IMAGE = "image"
    TEXT_LINK = "text_link"
    TEXT = "text"


@dataclass(frozen=True)
class InboxListConfig:
    """Parser settings (data-model.md ``InboxListConfig``)."""

    bullet_markers: tuple[str, ...] = ("-", "*")
    header_end: str = r"^---\s*$"
    strip_params: tuple[str, ...] = ("utm_*", "fbclid", "gclid", "igshid", "mc_*")
    comments_ignored: bool = True

    @classmethod
    def from_mapping(cls, data: dict | None) -> InboxListConfig:
        data = dict(data or {})
        known = {"bullet_markers", "header_end", "strip_params", "comments_ignored"}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"unknown inbox list setting(s): {', '.join(unknown)}")
        for key in ("bullet_markers", "strip_params"):
            if key in data:
                data[key] = tuple(data[key])
        return cls(**data)


@dataclass(frozen=True)
class InboxEntry:
    """One entry: ``raw`` = its exact lines (joined with ``\\n``), ``line`` = its first line (1-based)."""

    raw: str
    kind: EntryKind
    resource: str
    title: str
    line: int

    @property
    def item_id(self) -> str:
        return item_id_for(self.resource)


@dataclass
class ListItem:
    """One triage item of the list: the first entry of a resource, plus the raw text of its duplicates."""

    id: str
    kind: EntryKind
    resource: str
    title: str
    raw: str
    line: int
    raws: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "resource": self.resource,
            "title": self.title,
            "raw": self.raw,
            "raws": list(self.raws),
            "line": self.line,
            "source": "list",
        }


IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp", ".svg", ".avif", ".tif", ".tiff"}
)
_URL = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_EMBED = re.compile(r"^!\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]$")
_MD_IMAGE = re.compile(r"^!\[[^\]]*\]\(([^)\s]+)\)$")
_MD_LINK = re.compile(r"^\[([^\]]*)\]\((https?://[^)\s]+)\)$", re.IGNORECASE)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TITLE_EDGE = " \t-:–—|,;"


def normalize_url(url: str, strip_params: Sequence[str] = InboxListConfig.strip_params) -> str:
    """The URL without tracking parameters (``utm_*``, ``fbclid``, ...); the rest stays as written.

    Scheme and host are lower-cased, the path, the other parameters (and their order and encoding) and the
    fragment are kept. A URL that cannot be split is returned stripped.
    """
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return url
    kept = [
        seg for seg in parts.query.split("&") if seg and not _is_tracking(seg.partition("=")[0], strip_params)
    ]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, "&".join(kept), parts.fragment)
    )


def _is_tracking(name: str, patterns: Sequence[str]) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, p.lower()) for p in patterns)


def _clean_url(raw: str) -> str:
    """A URL found in text: trailing punctuation and an unbalanced closing bracket are not part of it."""
    url = raw
    while url:
        if url[-1] in ".,;:!?'\"":
            url = url[:-1]
        elif url[-1] == ")" and url.count(")") > url.count("("):
            url = url[:-1]
        else:
            break
    return url


def _first_url(text: str) -> str | None:
    match = _URL.search(text)
    return _clean_url(match.group(0)) if match else None


def _image_target(content: str) -> str | None:
    match = _EMBED.match(content) or _MD_IMAGE.match(content)
    if not match:
        return None
    target = match.group(1).strip()
    name = target.rsplit("/", 1)[-1]
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return target if ext in IMAGE_EXTENSIONS else None


def _classify(content: str, continuation: Sequence[str], cfg: InboxListConfig) -> tuple[EntryKind, str, str]:
    """``(kind, resource, title)`` from the first line (after the bullet) and the continuation lines."""
    content = content.strip()
    target = _image_target(content)
    if target:
        return EntryKind.IMAGE, target, target.rsplit("/", 1)[-1]
    md = _MD_LINK.match(content)
    if md:
        return (
            EntryKind.TEXT_LINK,
            normalize_url(_clean_url(md.group(2)), cfg.strip_params),
            md.group(1).strip(),
        )
    bare = content[1:-1] if content.startswith("<") and content.endswith(">") else content
    if _URL.fullmatch(bare):
        return EntryKind.LINK, normalize_url(_clean_url(bare), cfg.strip_params), ""
    inline = _first_url(content)
    if inline:
        title = _URL.sub(" ", content).strip(_TITLE_EDGE)
        title = " ".join(title.split())
        kind = EntryKind.TEXT_LINK if title else EntryKind.LINK
        return kind, normalize_url(inline, cfg.strip_params), title
    for line in continuation:
        url = _first_url(line)
        if url:
            return EntryKind.TEXT_LINK, normalize_url(url, cfg.strip_params), " ".join(content.split())
    text = " ".join([content, *continuation]).split()
    joined = " ".join(text)
    return EntryKind.TEXT, joined, joined


def _strip_comments(lines: list[str], enabled: bool) -> list[tuple[str, bool]]:
    """``(content, comment_only)`` per line: HTML comments (also across lines) removed from the content."""
    if not enabled:
        return [(ln, False) for ln in lines]
    out: list[tuple[str, bool]] = []
    in_comment = False
    for ln in lines:
        text, touched = ln, False
        if in_comment:
            touched = True
            end = text.find("-->")
            if end < 0:
                out.append(("", True))
                continue
            text, in_comment = text[end + 3 :], False
        cleaned = _COMMENT.sub("", text)
        touched = touched or cleaned != text
        text = cleaned
        start = text.find("<!--")
        if start >= 0:
            text, in_comment, touched = text[:start], True, True
        out.append((text, touched and not text.strip()))
    return out


def _front_matter_end(lines: list[str]) -> int:
    """Index of the first line after a leading YAML front matter block (0 when there is none)."""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                return i + 1
    return 0


def _bullet_body(line: str, markers: Sequence[str]) -> str:
    """The text of a bullet line after its marker."""
    for m in markers:
        if line == m:
            return ""
        if line.startswith(m + " "):
            return line[len(m) + 1 :]
    return line


def parse_list(text: str, config: InboxListConfig | None = None) -> list[InboxEntry]:
    """The entries of the list in file order (duplicates included).

    * the header (front matter, title, intro) ends at the first ``header_end`` line that comes before the
      first bullet (or after a title above it); a list with no header starts at the first bullet and a later rule
      only ends the entry above;
    * an entry starts with a bullet marker followed by a space and runs over the following non-blank,
      non-bullet lines (a URL on the next, unindented line belongs to the entry above);
    * ``<!-- comments -->`` are never entries and end the entry above;
    * an unknown shape is an entry of kind ``text`` (nothing is dropped).
    """
    cfg = config or InboxListConfig()
    lines = text.splitlines()
    start = _front_matter_end(lines)
    header_end = re.compile(cfg.header_end)
    markers = tuple(m + " " for m in cfg.bullet_markers)
    marker_alone = tuple(cfg.bullet_markers)

    def is_bullet(line: str) -> bool:
        return line.startswith(markers) or line in marker_alone

    stripped = _strip_comments(lines, cfg.comments_ignored)
    first_bullet = next(
        (i for i in range(start, len(lines)) if is_bullet(stripped[i][0]) and not stripped[i][1]),
        len(lines),
    )
    # The rule ends the header when there is a header: text (a title) above the first bullet, or the rule
    # itself above it. Bullets with nothing above them are a list without a header, and a later rule is
    # just a line of the list, never a reason to drop the entries above it.
    first = first_bullet
    has_header = any(
        header_end.match(lines[i]) or (lines[i].strip() and not stripped[i][1])
        for i in range(start, first_bullet)
    )
    if has_header:
        first = next((i + 1 for i in range(start, len(lines)) if header_end.match(lines[i])), first_bullet)
    entries: list[InboxEntry] = []
    current: list[int] = []

    def flush() -> None:
        if not current:
            return
        content = _bullet_body(stripped[current[0]][0], cfg.bullet_markers)
        cont = [stripped[i][0].strip() for i in current[1:]]
        if content.strip() or cont:
            kind, resource, title = _classify(content, cont, cfg)
            raw = "\n".join(lines[i].rstrip("\r") for i in current)
            entries.append(InboxEntry(raw, kind, resource, title, current[0] + 1))
        current.clear()

    for i in range(first, len(lines)):
        content, comment_only = stripped[i]
        if comment_only or header_end.match(lines[i]):  # a rule inside the list ends the entry, is no entry
            flush()
        elif not lines[i].strip():
            flush()
        elif is_bullet(content):
            flush()
            current.append(i)
        elif current:
            current.append(i)
        # text before the first bullet below the header is not an entry
    flush()
    return entries


def list_items(entries: Iterable[InboxEntry]) -> list[ListItem]:
    """One item per resource: a duplicate entry adds its raw text to the first one (it is removed with it)."""
    items: dict[str, ListItem] = {}
    for e in entries:
        item = items.get(e.item_id)
        if item is None:
            items[e.item_id] = ListItem(e.item_id, e.kind, e.resource, e.title, e.raw, e.line, [e.raw])
        else:
            item.raws.append(e.raw)
    return list(items.values())


@dataclass
class RemoveResult:
    text: str
    removed: list[str]
    not_found: list[str]


def remove_entries(text: str, raws: Sequence[str], config: InboxListConfig | None = None) -> RemoveResult:
    """Remove entries from the list text, matched by their raw text (never by line number).

    Only the lines of the removed entries disappear; every other byte (header, comments, blank lines,
    entries added meanwhile, line endings) stays. A raw text that matches no entry is reported in
    ``not_found`` and changes nothing. The same raw text given twice removes two equal entries.
    """
    entries = parse_list(text, config)
    taken: set[int] = set()
    removed: list[str] = []
    not_found: list[str] = []
    for raw in raws:
        target = raw.replace("\r\n", "\n").rstrip("\n")
        hit = next((i for i, e in enumerate(entries) if i not in taken and e.raw == target), None)
        if hit is None:
            not_found.append(raw)
            continue
        taken.add(hit)
        removed.append(raw)
    if not taken:
        return RemoveResult(text, removed, not_found)
    drop: set[int] = set()
    for i in taken:
        e = entries[i]
        drop.update(range(e.line - 1, e.line - 1 + e.raw.count("\n") + 1))
    kept = [ln for idx, ln in enumerate(text.splitlines(keepends=True)) if idx not in drop]
    return RemoveResult("".join(kept), removed, not_found)


def archive_text(existing: str | None, raws: Sequence[str], day: date) -> str:
    """The dated archive file after appending the removed entries (their raw text, unchanged)."""
    base = existing if existing is not None else f"# Inbox archive {day.isoformat()}\n\n"
    if base and not base.endswith("\n"):
        base += "\n"
    return base + "".join(f"{raw}\n" for raw in raws)
