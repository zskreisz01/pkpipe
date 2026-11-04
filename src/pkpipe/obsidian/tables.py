"""Lossless markdown table editing for the triage file and the processing list (spec 004 SC-001, T004/T005).

Only rows that really change are re-serialised. Every other line, including prose, other tables and rows
edited by hand in Obsidian, is written back byte for byte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SPLIT = re.compile(r"(?<!\\)\|")
_SEP_CELL = re.compile(r"^:?-{3,}:?$")


def split_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]
    return [c.strip().replace("\\|", "|") for c in _SPLIT.split(stripped)]


def join_cells(cells: list[str]) -> str:
    safe = [c.replace("\r", " ").replace("\n", " ").replace("|", "\\|") for c in cells]
    return "| " + " | ".join(safe) + " |"


@dataclass
class _Line:
    raw: str
    cells: dict[str, str] | None = None
    orig: dict[str, str] | None = None


class TableFile:
    """A markdown document containing one table with a known header."""

    def __init__(
        self,
        text: str | None,
        columns: tuple[str, ...],
        key: str,
        preamble: str = "",
        optional: tuple[str, ...] = (),
    ):
        """``optional`` columns are written only when a row needs one (a non-empty value): a file without
        them stays byte-identical, a row that needs one widens the header and every row once. Their cells
        read as empty when the header does not have them."""
        self.columns = columns + optional
        self._header_columns = columns
        self._optional = optional
        self._widened = not optional  # nothing to widen without optional columns
        self._rewrite_all = False
        self.key = key
        self.invalid: list[tuple[int, str]] = []  # (1-based line number, raw line)
        self._eol = "\r\n" if text and "\r\n" in text else "\n"
        self._lines: list[_Line] = []
        self._header_idx: int | None = None
        self._last_row_idx: int | None = None
        if text is None:
            text = preamble
        self._parse(text)

    def _parse(self, text: str) -> None:
        raw_lines = text.splitlines(keepends=True)
        in_table = False
        for idx, raw in enumerate(raw_lines):
            line = _Line(raw)
            self._lines.append(line)
            body = raw.strip()
            if self._header_idx is None:
                names = [c.lower() for c in split_cells(body)] if body.startswith("|") else []
                if self._is_header(names):
                    self._header_idx = idx
                    self._header_columns = self.columns[: len(names)]
                    self._widened = len(names) == len(self.columns)
                    in_table = True
                continue
            if not in_table:
                continue
            if not body.startswith("|"):
                in_table = False
                continue
            cells = split_cells(body)
            if idx == self._header_idx + 1 and all(_SEP_CELL.match(c) for c in cells):
                self._last_row_idx = idx
                continue
            if len(cells) != len(self._header_columns) or not cells[self.columns.index(self.key)]:
                self.invalid.append((idx + 1, body))
                self._last_row_idx = idx
                continue
            cells = cells + [""] * (len(self.columns) - len(cells))
            line.cells = dict(zip(self.columns, cells, strict=True))
            line.orig = dict(line.cells)
            self._last_row_idx = idx

    def _is_header(self, names: list[str]) -> bool:
        required = len(self.columns) - len(self._optional)
        if names[:required] != list(self.columns[:required]):
            return False
        return names[required:] == list(self._optional[: len(names) - required])

    def _widen_for(self, cells: dict[str, str]) -> None:
        if not self._widened and any(cells.get(c) for c in self._optional):
            self._widened = True
            self._header_columns = self.columns
            self._rewrite_all = self._header_idx is not None

    def rows(self) -> list[dict[str, str]]:
        return [dict(ln.cells) for ln in self._lines if ln.cells is not None]

    def get(self, key: str) -> dict[str, str] | None:
        for ln in self._lines:
            if ln.cells is not None and ln.cells[self.key] == key:
                return dict(ln.cells)
        return None

    def upsert(self, cells: dict[str, str]) -> str:
        """Insert or replace the row with the same key. Returns ``inserted``, ``updated`` or ``unchanged``."""
        self._widen_for(cells)
        for ln in self._lines:
            if ln.cells is not None and ln.cells[self.key] == cells[self.key]:
                if ln.cells == cells:
                    return "unchanged"
                ln.cells = dict(cells)
                return "updated"
        self._insert(_Line("", dict(cells), None))
        return "inserted"

    def _insert(self, line: _Line) -> None:
        if self._header_idx is None:
            if self._lines and not self._lines[-1].raw.endswith(("\n", "\r")):
                self._lines[-1].raw += self._eol
            if self._lines and self._lines[-1].raw.strip():
                self._lines.append(_Line(self._eol))
            header = join_cells(list(self._header_columns)) + self._eol
            sep = join_cells(["---"] * len(self._header_columns)) + self._eol
            self._lines.extend([_Line(header), _Line(sep)])
            self._header_idx = len(self._lines) - 2
            self._last_row_idx = len(self._lines) - 1
        at = (self._last_row_idx if self._last_row_idx is not None else self._header_idx) + 1
        prev = self._lines[at - 1]
        if not prev.raw.endswith(("\n", "\r")):
            prev.raw += self._eol
        self._lines.insert(at, line)
        self._last_row_idx = at

    def render(self) -> str:
        out: list[str] = []
        for idx, ln in enumerate(self._lines):
            ending = ln.raw[len(ln.raw.rstrip("\r\n")) :] or self._eol
            if (
                self._rewrite_all
                and self._header_idx is not None
                and idx in (self._header_idx, self._header_idx + 1)
            ):
                names = (
                    list(self._header_columns)
                    if idx == self._header_idx
                    else ["---"] * len(self._header_columns)
                )
                out.append(join_cells(names) + ending)
            elif ln.cells is not None and (ln.cells != ln.orig or self._rewrite_all):
                out.append(join_cells([ln.cells[c] for c in self._header_columns]) + ending)
            else:
                out.append(ln.raw)
        return "".join(out)
