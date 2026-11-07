"""The pkpipe todo block of a daily journal (spec 024).

The journal is the most sensitive note of the vault (design rules 5.7). The pipeline therefore only ever
owns **one block** of it: the lines between :data:`BEGIN` and :data:`END` below a heading. This module is
the whole write rule, pure text in and text out:

* the block is replaced as a whole, so writing the same day twice never duplicates a line;
* no byte outside the markers is changed; a note without markers gets the heading and the block
  (below an existing identical heading when there is one, else at the end);
* a note that does not exist is created with the front matter ``type: journal`` and the section only;
* markers that do not form exactly one begin/end pair are refused, never repaired by guessing.
"""

from __future__ import annotations

import re

BEGIN = "<!-- pkpipe-todo:begin -->"
END = "<!-- pkpipe-todo:end -->"
DEFAULT_HEADING = "## pkpipe todo"
MAX_LINES = 20
MAX_LINE_CHARS = 300
_HEADING = re.compile(r"^#{1,6} [^\r\n<>]{1,60}$")


class JournalTodoError(ValueError):
    """The lines, the heading or the markers of the note are not acceptable."""


def check_heading(heading: str) -> str:
    if not isinstance(heading, str) or not _HEADING.match(heading.strip()):
        raise JournalTodoError("the heading must be one Markdown heading line of at most 60 characters")
    return heading.strip()


def check_lines(lines: object) -> list[str]:
    """Generated todo lines only: ``- ...`` single lines, no markers, no HTML comments."""
    if not isinstance(lines, list) or len(lines) > MAX_LINES:
        raise JournalTodoError(f"give a list of at most {MAX_LINES} lines")
    out: list[str] = []
    for raw in lines:
        if not isinstance(raw, str):
            raise JournalTodoError("every line must be a string")
        if any(c in raw for c in "\r\n\x00"):
            raise JournalTodoError("a line is one plain line: no line breaks and no HTML comments")
        line = raw.strip()
        if not line.startswith("- ") or len(line) > MAX_LINE_CHARS:
            raise JournalTodoError(f"a line starts with '- ' and has at most {MAX_LINE_CHARS} characters")
        if "<!--" in line or "-->" in line:
            raise JournalTodoError("a line is one plain line: no line breaks and no HTML comments")
        out.append(line)
    return out


def _block(lines: list[str]) -> str:
    return "\n".join([BEGIN, *lines, END]) if lines else f"{BEGIN}\n{END}"


def new_note(heading: str, lines: list[str]) -> str:
    return f"---\ntype: journal\n---\n\n{heading}\n{_block(lines)}\n"


def replace_block(text: str | None, heading: str, lines: list[str]) -> str:
    """The journal text with its pkpipe todo block set to ``lines`` (see the module docstring)."""
    heading = check_heading(heading)
    lines = check_lines(lines)
    if text is None:
        return new_note(heading, lines)
    begins, ends = text.count(BEGIN), text.count(END)
    if begins == 0 and ends == 0:
        return _insert_section(text, heading, lines)
    start = text.find(BEGIN)
    stop = text.find(END)
    if begins != 1 or ends != 1 or stop < start:
        raise JournalTodoError("the journal has damaged pkpipe todo markers: fix them by hand")
    return text[:start] + _block(lines) + text[stop + len(END) :]


def _insert_section(text: str, heading: str, lines: list[str]) -> str:
    block = _block(lines)
    found = re.search(rf"^{re.escape(heading)}[ \t]*\r?$", text, re.MULTILINE)
    if found:  # the owner already has the section (for example from the template): the block goes below it
        end = found.end()
        return text[:end] + "\n" + block + (text[end:] or "\n")
    tail = "" if not text or text.endswith("\n") else "\n"
    return f"{text}{tail}\n{heading}\n{block}\n"
