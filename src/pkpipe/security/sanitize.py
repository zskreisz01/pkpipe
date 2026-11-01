"""Sanitizer of secure fetch (spec 008 FR-003): rendered HTML -> visible text the Reader may see.

Removed: hidden elements (``hidden``, ``aria-hidden``, hidden inputs, inline styles and simple ``<style>``
rules that hide by class, id or tag: ``display:none``, ``visibility:hidden``, ``font-size:0``, ``opacity:0``,
off-screen positioning, transparent text, zero-size clipping), comments, non-content elements (script, style,
template, iframe, svg, ...), zero-width and bidi control characters and Unicode tag characters (U+E0000 -
U+E007F, "ASCII smuggling").

What was removed is not thrown away silently: it is counted and the hidden text (including tag characters
decoded back to ASCII) is returned as ``hidden_text`` so the heuristics can score it. A page that hides
instructions from the human reader is itself a signal. Only stdlib (``html.parser``) is used, so the
sanitizer is core code and runs the same in the step, in the eval and in tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

#: Elements whose content is never page text.
DROP_ELEMENTS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "iframe",
        "frame",
        "frameset",
        "object",
        "embed",
        "svg",
        "math",
        "canvas",
        "select",
        "textarea",
        "button",
        "head",
    }
)
#: Dropped elements whose text a browser never shows but an attacker can fill: scanned as hidden text.
HIDDEN_TEXT_ELEMENTS = frozenset({"noscript", "template", "textarea", "select", "button", "object", "math"})
VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
BLOCK_ELEMENTS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "main",
        "header",
        "footer",
        "aside",
        "nav",
        "ul",
        "ol",
        "table",
        "tr",
        "blockquote",
        "pre",
        "figure",
        "figcaption",
        "dl",
        "dt",
        "dd",
        "br",
        "hr",
        "body",
        "html",
        "details",
        "summary",
    }
)
HEADINGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}

_HIDING_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*(hidden|collapse)|font-size\s*:\s*0(?![.\d]*[1-9])"
    r"|opacity\s*:\s*0(?![.\d]*[1-9])|(?:^|;)\s*(?:max-)?height\s*:\s*0(?:px)?\s*(?:;|$)[^}]*overflow\s*:\s*hidden"
    r"|(?:left|top|text-indent)\s*:\s*-\d{3,}"
    r"|clip\s*:\s*rect\(\s*0|clip-path\s*:\s*inset\(\s*50%"
    r"|color\s*:\s*transparent|transform\s*:\s*scale\(\s*0\s*\)",
    re.IGNORECASE,
)
_STYLE_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_SIMPLE_SELECTOR = re.compile(r"^(?:[a-z][a-z0-9]*)?(?:[.#][\w-]+)?$", re.IGNORECASE)

#: Zero-width, soft hyphen, bidi controls and other invisible format characters.
_INVISIBLE = re.compile("[­᠎​-‏‪-‮⁠-⁤⁦-⁩﻿]")
_TAG_CHARS = re.compile("[\U000e0000-\U000e007f]+")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")


@dataclass
class SanitizedPage:
    title: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)
    removed: dict[str, int] = field(default_factory=dict)
    hidden_text: str = ""
    truncated: bool = False

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "text": self.text,
            "links": self.links,
            "meta": self.meta,
            "removed": self.removed,
            "hidden_text": self.hidden_text,
            "truncated": self.truncated,
        }


def strip_invisible(text: str) -> tuple[str, dict[str, int], str]:
    """(clean text, counts, decoded smuggled ASCII). Tag characters map back to ASCII (U+E0041 -> 'A')."""
    smuggled = "".join(
        chr(ord(c) - 0xE0000) for m in _TAG_CHARS.findall(text) for c in m if 0x20 <= ord(c) - 0xE0000 < 0x7F
    )
    tag_count = sum(len(m) for m in _TAG_CHARS.findall(text))
    text = _TAG_CHARS.sub("", text)
    zero = len(_INVISIBLE.findall(text))
    text = _INVISIBLE.sub("", text)
    return text, {"zero_width": zero, "tag_chars": tag_count}, smuggled


def _hiding_rules(css: str) -> set[str]:
    """Simple selectors (``tag``, ``.cls``, ``#id``, ``tag.cls``) that a stylesheet hides."""
    out: set[str] = set()
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for selectors, body in _STYLE_RULE.findall(css):
        if not _HIDING_STYLE.search(body):
            continue
        for sel in selectors.split(","):
            sel = sel.strip().lower()
            if sel and _SIMPLE_SELECTOR.match(sel):
                out.add(sel)
    return out


class _Parser(HTMLParser):
    def __init__(self, base_url: str | None, hidden_selectors: set[str]):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.hidden_selectors = hidden_selectors
        self.stack: list[tuple[str, bool, bool]] = []  # (tag, hidden, dropped)
        self.out: list[str] = []
        self.hidden: list[str] = []
        self.links: list[str] = []
        self.meta: dict[str, str] = {}
        self.title_parts: list[str] = []
        self.counts = {"hidden_elements": 0, "comments": 0, "non_content": 0}
        self.list_depth = 0

    # -- state ------------------------------------------------------------------------------------------
    @property
    def _hidden(self) -> bool:
        return any(h for _, h, _ in self.stack)

    @property
    def _dropped(self) -> bool:
        return any(d for _, _, d in self.stack)

    def _in(self, tag: str) -> bool:
        return any(t == tag for t, _, _ in self.stack)

    def _is_hidden(self, tag: str, attrs: dict[str, str]) -> bool:
        if "hidden" in attrs or attrs.get("aria-hidden", "").lower() == "true":
            return True
        if tag == "input" and attrs.get("type", "").lower() == "hidden":
            return True
        if _HIDING_STYLE.search(attrs.get("style", "")):
            return True
        if self.hidden_selectors:
            names = {tag}
            classes = attrs.get("class", "").lower().split()
            names |= {f".{c}" for c in classes} | {f"{tag}.{c}" for c in classes}
            if attrs.get("id"):
                names |= {f"#{attrs['id'].lower()}", f"{tag}#{attrs['id'].lower()}"}
            if names & self.hidden_selectors:
                return True
        return False

    # -- parser callbacks -------------------------------------------------------------------------------
    def handle_starttag(self, tag, attrs_list):
        attrs = {k.lower(): (v or "") for k, v in attrs_list}
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in ("description", "og:title", "og:description", "og:type", "og:video", "og:site_name"):
                self.meta[key] = attrs.get("content", "")[:500]
            return
        hidden = not self._hidden and not self._dropped and self._is_hidden(tag, attrs)
        dropped = tag in DROP_ELEMENTS
        if hidden:
            self.counts["hidden_elements"] += 1
        elif dropped and tag not in ("head",) and not self._dropped:
            self.counts["non_content"] += 1
        if tag == "input" and hidden and attrs.get("value"):
            self.hidden.append(attrs["value"])
        if tag == "a" and not self._hidden and not self._dropped:
            href = attrs.get("href", "").strip()
            absolute = urljoin(self.base_url, href) if self.base_url else href
            if urlsplit(absolute).scheme in ("http", "https") and absolute not in self.links:
                self.links.append(absolute)
        if tag in VOID_ELEMENTS:
            if tag in ("br", "hr"):
                self.out.append("\n")
            return
        self.stack.append((tag, hidden, dropped))
        if self._hidden or self._dropped:
            return
        if tag in HEADINGS:
            self.out.append("\n\n" + HEADINGS[tag] + " ")
        elif tag == "li":
            self.out.append("\n" + "  " * max(self.list_depth - 1, 0) + "- ")
        elif tag in ("ul", "ol"):
            self.list_depth += 1
            self.out.append("\n")
        elif tag in BLOCK_ELEMENTS:
            self.out.append("\n\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in VOID_ELEMENTS or not self._in(tag):
            return
        while self.stack:
            t, _, _ = self.stack.pop()
            if t in ("ul", "ol") and not self._hidden and not self._dropped:
                self.list_depth = max(self.list_depth - 1, 0)
            if t == tag:
                break
        if not self._hidden and not self._dropped and (tag in BLOCK_ELEMENTS or tag in HEADINGS):
            self.out.append("\n")

    def handle_data(self, data):
        if self._in("title"):
            self.title_parts.append(data)
            return
        if self._in("style") and not self._hidden:
            return
        if self._hidden:
            self.hidden.append(data)
            return
        if self._dropped:
            if any(t in HIDDEN_TEXT_ELEMENTS for t, _, _ in self.stack):
                self.hidden.append(data)  # invisible to the reader of the page, scanned by the heuristics
            return
        self.out.append(data)

    def handle_comment(self, data):
        self.counts["comments"] += 1
        self.hidden.append(data)


def _tidy(text: str) -> str:
    lines = [_WS.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def sanitize_html(html: str, base_url: str | None = None, *, max_chars: int | None = None) -> SanitizedPage:
    """Visible text of a rendered page as light Markdown (headings, lists), plus what was removed."""
    html, inv_counts, smuggled = strip_invisible(html or "")
    css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, flags=re.S | re.I))
    parser = _Parser(base_url, _hiding_rules(css))
    parser.feed(html)
    parser.close()
    text = _tidy("".join(parser.out))
    truncated = bool(max_chars and len(text) > max_chars)
    if truncated:
        text = text[:max_chars].rstrip() + "\n\n[truncated]"
    hidden = _tidy(" ".join(parser.hidden) + (" " + smuggled if smuggled else ""))
    return SanitizedPage(
        title=_tidy(" ".join(parser.title_parts))[:300],
        text=text,
        links=parser.links[:50],
        meta=parser.meta,
        removed={**parser.counts, **inv_counts},
        hidden_text=hidden[:20_000],
        truncated=truncated,
    )


def sanitize_text(text: str, *, max_chars: int | None = None) -> SanitizedPage:
    """Plain text or Markdown (no HTML): only the invisible characters are removed."""
    clean, counts, smuggled = strip_invisible(text or "")
    clean = _tidy(clean)
    truncated = bool(max_chars and len(clean) > max_chars)
    if truncated:
        clean = clean[:max_chars].rstrip() + "\n\n[truncated]"
    return SanitizedPage(
        text=clean,
        removed={"hidden_elements": 0, "comments": 0, "non_content": 0, **counts},
        hidden_text=smuggled,
        truncated=truncated,
    )
