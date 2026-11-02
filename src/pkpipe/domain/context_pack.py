"""Context packs (spec 011): compact, current descriptions of the owner's projects that sessions receive.

Pure logic of the weekly ``context_refresh`` flow; the sessions only summarize, code decides what is written
(constitution V):

* **Anonymization of the enterprise pack.** Names that must never reach a web query or a remote prompt
  (clients, projects, people) come from three places: the front matter and titles of the enterprise notes
  (:func:`harvest_terms`), the names the local session reports (:func:`session_terms`, kept only when they
  occur in the notes) and the owner's own blocked terms. :func:`scrub` replaces every occurrence in the
  generated text; :func:`leaks` is the final check with the very function the query guard of spec 010 uses,
  and a line that still matches is dropped. The same terms are appended to the query guard's terms file
  (:func:`merge_terms_file`), so the web queries of later sessions are blocked on them.
* **Manual edits survive.** A written pack starts with a hash of its generated part. When the hash still
  matches, the refresh may replace the generated part; when the owner edited it (or wrote the file
  themselves), the file is left alone and the new version is only proposed as a diff
  (:func:`merge_pack`). Text below ``<!-- pkpipe:manual -->`` is the owner's and is never rewritten.
* **Token limit.** Packs are rendered within a token budget; the last projects are dropped, never cut in half.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from pkpipe.security import query_guard

HEADER_RE = re.compile(r"^<!-- pkpipe:context-pack sha256=([0-9a-f]{16}) -->[ \t]*$")
MANUAL_MARKER = "<!-- pkpipe:manual -->"
PROPOSED_SUFFIX = ".proposed"
MARKER_LINE = re.compile(r"^[ \t]*<!-- pkpipe:[^>]*-->[ \t]*$")

#: Front matter fields that name a client, project or person (lower case).
DEFAULT_NAME_FIELDS: dict[str, str] = {
    "client": "client",
    "customer": "client",
    "company": "client",
    "account": "client",
    "project": "project",
    "programme": "project",
    "program": "project",
    "codename": "project",
    "aliases": "name",
    "alias": "name",
    "people": "person",
    "person": "person",
    "contacts": "person",
    "contact": "person",
    "stakeholders": "person",
    "sponsor": "person",
    "owner": "person",
    "lead": "person",
    "architect": "person",
    "architects": "person",
    "attendees": "person",
    "manager": "person",
    "author": "person",
    "reviewer": "person",
    "reviewers": "person",
    "client contact": "person",
}
PLACEHOLDER = {"client": "[client]", "project": "[project]", "person": "[person]", "name": "[name]"}
MIN_TERM_CHARS = 3
MAX_TERMS = 200
#: Words that are no name of their own: stripped from the front of a term, never a variant by themselves.
GENERIC_WORDS = frozenset(
    "the a an project projekt client customer programme program platform system service services team "
    "data cloud migration modernisation modernization portal app application tool tools internal "
    "enterprise personal group holding".split()
)
#: Note titles that name no project ("Roadmap", "Meeting notes"): never a term of their own.
GENERIC_TITLES = frozenset(
    "overview notes note roadmap readme index architecture status plan planning backlog meeting meetings "
    "retro retrospective design requirements summary todo tasks log journal context stack retail banking "
    "bank "
    "payments payment analytics infrastructure backend frontend api web mobile security ops devops reporting "
    "integration automation monitoring".split()
)
LEGAL_SUFFIXES = frozenset(
    "gmbh ag inc ltd llc corp corporation sa bv nv plc co kg oy ab group holding holdings".split()
)


# -- text helpers --------------------------------------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    """Four characters per token: close enough for a budget, and the same in every environment."""
    return (len(text) + 3) // 4


def clip(text: str, chars: int) -> str:
    """At most ``chars`` characters, cut at a word boundary."""
    text = " ".join(str(text).split())
    if len(text) <= chars:
        return text
    cut = text[: max(chars - 1, 0)].rsplit(" ", 1)[0].rstrip(",;:- ")
    return (cut or text[:chars]).rstrip() + "…"


def digest(body: str) -> str:
    return hashlib.sha256(body.strip("\n").encode("utf-8")).hexdigest()[:16]


def strip_markers(text: str) -> str:
    """The pack as a session sees it: without the bookkeeping comment lines (the owner's text stays)."""
    return "\n".join(ln for ln in text.splitlines() if not MARKER_LINE.match(ln)).strip()


# -- the pack file -------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ParsedPack:
    body: str
    manual: str
    header: str | None  # the hash recorded when the pack was generated; None for a hand-written file

    @property
    def edited(self) -> bool:
        """True when the generated part is not what the refresh wrote (the owner changed it, or wrote it)."""
        return self.header is None or self.header != digest(self.body)


def parse_pack(text: str) -> ParsedPack:
    lines = text.replace("\r\n", "\n").split("\n")
    header = None
    if lines and (m := HEADER_RE.match(lines[0])):
        header, lines = m.group(1), lines[1:]
    manual = ""
    for i, line in enumerate(lines):
        if line.strip() == MANUAL_MARKER:
            manual = "\n".join(lines[i + 1 :]).strip("\n")
            lines = lines[:i]
            break
    return ParsedPack("\n".join(lines).strip("\n"), manual, header)


def render_pack(body: str, manual: str = "") -> str:
    body = body.strip("\n")
    out = f"<!-- pkpipe:context-pack sha256={digest(body)} -->\n{body}\n"
    if manual.strip():
        out += f"\n{MANUAL_MARKER}\n{manual.strip(chr(10))}\n"
    return out


def unified_diff(old: str, new: str, old_name: str = "current", new_name: str = "proposed") -> str:
    lines = difflib.unified_diff(
        old.splitlines(), new.splitlines(), fromfile=old_name, tofile=new_name, lineterm="", n=2
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class MergeResult:
    """``status``: ``created``, ``updated`` (the refresh replaced its own, unedited text), ``unchanged``,
    ``proposed`` (the owner edited the generated part: ``text`` is None, ``proposal`` holds the new file).
    ``diff`` is current -> new (empty when nothing changes)."""

    status: str
    text: str | None
    proposal: str | None
    diff: str

    @property
    def changed(self) -> bool:
        return self.status != "unchanged"


def merge_pack(current: str | None, body: str) -> MergeResult:
    """Decide what to write for a freshly generated ``body`` given the pack file as it is now."""
    fresh = body.strip("\n")
    if current is None or not current.strip():
        return MergeResult("created", render_pack(fresh), None, unified_diff("", fresh, "(none)", "new"))
    old = parse_pack(current)
    if old.body == fresh:
        return MergeResult("unchanged", None, None, "")
    new_file = render_pack(fresh, old.manual)
    diff = unified_diff(old.body, fresh)
    if old.edited:
        return MergeResult("proposed", None, new_file, diff)
    return MergeResult("updated", new_file, None, diff)


# -- names that must stay private ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Term:
    term: str
    kind: str = "name"


def _words(value: str) -> list[str]:
    return [w for w in re.split(r"\s+", value.strip()) if w]


def term_variants(term: Term) -> list[str]:
    """The term itself plus the forms people write: without a legal suffix or a leading generic word
    ("Acme Corp" -> "Acme", "Project Falcon" -> "Falcon"), the capitalised first word of a longer name
    ("Brightwater Bank" -> "Brightwater"), and for a person each name part."""
    words = _words(term.term)
    variants = [" ".join(words)]
    core = list(words)
    while len(core) > 1 and core[-1].casefold().strip(".,") in LEGAL_SUFFIXES:
        core.pop()
    while len(core) > 1 and core[0].casefold() in GENERIC_WORDS:
        core.pop(0)
    if (
        core != words
        and len(" ".join(core)) >= MIN_TERM_CHARS + 1
        and core[0].casefold() not in GENERIC_WORDS
    ):
        variants.append(" ".join(core))
    first = core[0] if core else ""
    if (
        term.kind != "person"
        and len(core) > 1
        and len(first) > MIN_TERM_CHARS + 1
        and first.casefold() not in GENERIC_WORDS | GENERIC_TITLES
        and first[:1].isupper()
    ):
        variants.append(first)  # "Marlow" for "Marlow Retail Group": the short form people write
    if term.kind == "person" and len(words) > 1:
        variants += [w for w in words if len(w) >= MIN_TERM_CHARS + 1 and w.casefold() not in GENERIC_WORDS]
    return [v for v in dict.fromkeys(variants) if len(query_guard.normalise(v).strip()) >= MIN_TERM_CHARS]


def _front_matter_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [p.strip() for p in re.split(r"[;\n]|,(?![^\[]*\])", value) if p.strip()]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _front_matter_values(v)]
    return []


def _wikilink(value: str) -> str:
    m = re.fullmatch(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]", value.strip())
    return m.group(1).strip() if m else value.strip()


def generic_title(title: str) -> bool:
    """A title made only of generic words ("Data platform notes", "2026-10 Roadmap") names no project."""
    words = [w.casefold() for w in re.split(r"[\W_]+", title) if w and not w.isdigit()]
    return not words or all(w in GENERIC_WORDS or w in GENERIC_TITLES for w in words)


_INLINE = re.compile(
    r"(?im)(?:^|[\n.;!?])[ \t>*_-]*((?:[a-z]+[ \t]+){0,2}[a-z]+)[*_]*[ \t]*:[ \t]*[*_]*[ \t]*([^\n.;!?]+)"
)


def _inline_names(body: str, fields: dict[str, str]) -> list[tuple[str, str]]:
    """``Client: Acme``, ``- **Project**: Falcon``, ``People: A, B`` and ``Delivery lead: C`` in the note body
    (at a line or sentence start): the owner marked these as names, like front matter. Values must start
    with a capital letter or a digit."""
    out: list[tuple[str, str]] = []
    for m in _INLINE.finditer(body or ""):
        label = m.group(1).casefold().split()
        kind = fields.get(" ".join(label)) or fields.get(label[-1])
        if not kind:
            continue
        for part in re.split(r"\s+(?:and|und|&)\s+|,", m.group(2)):
            name = _wikilink(part.strip(" *_\t"))
            if name and (name[0].isupper() or name[0].isdigit()) and len(name.split()) <= 6:
                out.append((name, kind))
    return out


def harvest_terms(
    notes: list[dict[str, Any]], name_fields: dict[str, str] | None = None, *, title_kind: str = "project"
) -> list[Term]:
    """Names the notes declare themselves: the configured front matter fields, labelled lines of the body
    (``Client: ...``) and the note's title."""
    fields = {k.casefold(): v for k, v in (name_fields or DEFAULT_NAME_FIELDS).items()}
    found: list[Term] = []
    for note in notes:
        stem = str(note.get("path", "")).rsplit("/", 1)[-1].removesuffix(".md").strip()
        for part in dict.fromkeys([stem, re.split(r"\s+[-\u2013\u2014:]\s+", stem)[0].strip()]):
            if part and not generic_title(part):
                found.append(Term(part, title_kind))
        for key, value in (note.get("front_matter") or {}).items():
            kind = fields.get(str(key).casefold())
            if kind:
                found += [Term(_wikilink(v), kind) for v in _front_matter_values(value) if _wikilink(v)]
        found += [Term(v, kind) for v, kind in _inline_names(str(note.get("body") or ""), fields)]
    return found


def session_terms(names: Any, notes_text: str) -> list[Term]:
    """Names the session reported. Only names that occur in the notes count: a made-up or generic word must
    not end up in the terms file (and so in blocked web queries or scrubbed packs)."""
    hay = query_guard.normalise(notes_text)
    out: list[Term] = []
    for raw in names if isinstance(names, list) else []:
        text = " ".join(str(raw).split())
        needle = query_guard.normalise(text)
        if len(needle.strip()) < MIN_TERM_CHARS or text.casefold() in GENERIC_WORDS:
            continue
        if needle in hay:
            out.append(Term(text, "name"))
    return out


def unique_terms(*groups: list[Term], limit: int = MAX_TERMS) -> list[Term]:
    """Deduplicated by normalised text (first kind wins), generic one-word terms and too short ones
    dropped."""
    seen: dict[str, Term] = {}
    for term in (t for g in groups for t in g):
        key = query_guard.normalise(term.term).strip()
        if len(key) < MIN_TERM_CHARS or key in GENERIC_WORDS or key in LEGAL_SUFFIXES or key in seen:
            continue
        seen[key] = term
    return list(seen.values())[:limit]


def all_variants(terms: list[Term]) -> list[tuple[str, str]]:
    """``(variant, kind)`` of every term, longest first (so "Acme Corp" is replaced before "Acme")."""
    pairs = {(v, t.kind) for t in terms for v in term_variants(t)}
    return sorted(pairs, key=lambda p: (-len(p[0]), p[0].casefold()))


def _fold(text: str) -> tuple[str, list[int]]:
    """Case and accent folded text with, for every folded character, the index of its original character."""
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        folded = unicodedata.normalize("NFKD", ch).casefold()
        for c in folded:
            if unicodedata.combining(c) or unicodedata.category(c) == "Cf":
                continue
            out.append(c)
            index.append(i)
    return "".join(out), index


def _pattern(variant: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in query_guard.normalise(variant).split()]
    return re.compile(r"(?<!\w)" + r"[\W_]+".join(words) + r"(?!\w)", re.UNICODE)


def scrub(text: str, terms: list[Term]) -> tuple[str, int]:
    """Replace every occurrence of a term (any variant; case, accent and punctuation insensitive, whole words,
    like the query guard) by a placeholder. Returns (text, replacements)."""
    folded, index = _fold(text)
    spans: list[tuple[int, int, str]] = []
    for variant, kind in all_variants(terms):
        for m in _pattern(variant).finditer(folded):
            if m.end() > m.start():
                spans.append((index[m.start()], index[m.end() - 1] + 1, PLACEHOLDER.get(kind, "[name]")))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    merged: list[tuple[int, int, str]] = []
    for start, end, label in spans:
        if merged and start < merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end, merged[-1][2])
            continue
        merged.append((start, end, label))
    for start, end, label in reversed(merged):
        text = text[:start] + label + text[end:]
    return text, len(merged)


def leaks(text: str, terms: list[Term]) -> bool:
    """The query guard's own match (spec 010), over every variant: the last gate before a pack is written."""
    return query_guard.check_arguments(text, [v for v, _ in all_variants(terms)]) is not None


# -- the terms file of the query guard -----------------------------------------------------------------------
def merge_terms_file(current: str | None, terms: list[Term], day: str) -> tuple[str, list[str]]:
    """Append the terms the file does not know yet (compared normalised); the owner's lines and comments stay
    and nothing is ever removed, so a name stays blocked after its project ended. Returns (text, added)."""
    text = current or ""
    known = {query_guard.normalise(t).strip() for t in query_guard.parse_terms(text)}
    added: list[str] = []
    for term in terms:
        key = query_guard.normalise(term.term).strip()
        if key and key not in known:
            known.add(key)
            added.append(" ".join(term.term.split()))
    if not added:
        return text, []
    if text and not text.endswith("\n"):
        text += "\n"
    return text + f"# added by context_refresh on {day}\n" + "\n".join(added) + "\n", added


# -- rendering -----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Rendered:
    body: str
    tokens: int
    projects: int
    trimmed: int  # projects left out to stay within the budget
    dropped: int  # lines removed because a private name survived the scrub
    scrubbed: int  # replacements made by the scrub


def _list(values: Any, limit: int = 8) -> list[str]:
    return [clip(v, 60) for v in (values if isinstance(values, list) else []) if str(v).strip()][:limit]


def project_line(project: dict[str, Any], kind: str) -> str:
    label = clip(project.get("name") or project.get("label") or "Project", 80)
    status = clip(project.get("status") or "", 40)
    line = (
        f"- **{label}**" + (f" ({status})" if status else "") + f": {clip(project.get('summary', ''), 280)}"
    )
    stack = _list(project.get("stack"))
    if stack:
        line += f" Stack: {', '.join(stack)}."
    constraints = _list(project.get("constraints"), 4)
    if kind == "enterprise" and constraints:
        line += f" Constraints: {'; '.join(constraints)}."
    return line


def render_body(
    kind: str, summary: dict[str, Any] | None, *, max_tokens: int, terms: list[Term] | None = None
) -> Rendered:
    """The generated part of a pack. ``kind`` is ``personal`` or ``enterprise``; the enterprise text is
    scrubbed of ``terms`` and verified before it is returned. An empty ``summary`` renders the empty
    pack."""
    terms = terms or []
    title = "# Personal context" if kind == "personal" else "# Enterprise context (anonymized)"
    heading = "## Projects" if kind == "personal" else "## Programmes"
    if not summary:
        body = f"{title}\n\nNo project notes were found, so this context is empty."
        return Rendered(body, estimate_tokens(body), 0, 0, 0, 0)
    scrubbed = dropped = 0

    def safe(text: str) -> str | None:
        nonlocal scrubbed, dropped
        if kind != "enterprise" or not terms:
            return text
        text, n = scrub(text, terms)
        scrubbed += n
        if leaks(text, terms):
            dropped += 1
            return None
        return text

    overview = safe(clip(summary.get("overview", ""), 600)) or ""
    head = f"{title}\n\n{overview}".rstrip() + f"\n\n{heading}\n"
    lines: list[str] = []
    candidates = [p for p in summary.get("projects") or [] if isinstance(p, dict)]
    trimmed = 0
    used = estimate_tokens(head)
    for project in candidates:
        line = safe(project_line(project, kind))
        if line is None:
            continue
        cost = estimate_tokens(line) + 1
        if used + cost > max_tokens:
            trimmed += 1
            continue
        lines.append(line)
        used += cost
    body = head + "\n".join(lines)
    return Rendered(body.rstrip(), estimate_tokens(body), len(lines), trimmed, dropped, scrubbed)


def review_note(name: str, status: str, diff: str) -> str:
    """The owner's review note (a diff in a code block, how to accept)."""
    pack, proposed = f"contexts/{name}.md", f"contexts/{name}.md{PROPOSED_SUFFIX}"
    if status == "proposed":
        how = (
            f"You edited the generated part of `{pack}`, so it was left as it is. The refresh wrote its new "
            f"version to `{proposed}` in your config overlay. Merge what you want into the pack, or replace "
            f"the pack with the proposal (`mv {proposed} {pack}`). Text below `{MANUAL_MARKER}` is never "
            "rewritten, so lasting notes belong there."
        )
    elif status == "updated":
        how = (
            f"`{pack}` was updated (your text below `{MANUAL_MARKER}` is untouched). Edit the pack where it "
            "is wrong; edits to the generated part turn the next refresh into a proposal instead of an "
            "overwrite."
        )
    else:
        how = f"`{pack}` was created."
    return f"# Context pack `{name}`: {status}\n\n{how}\n\n```diff\n{diff or '(no changes)'}\n```\n"
