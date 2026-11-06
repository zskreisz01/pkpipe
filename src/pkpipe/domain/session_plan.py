"""Category session plans (spec 010): validation rules and the deterministic note rendering.

A category's ``sessions`` list in ``categories.json`` is its session plan: ordered sessions, each with a fixed
system prompt, a category instruction, at most one context pack, an optional tool allow list, declared inputs
and an optional output schema (data-model ``SessionSpec``). This module is pure: it checks a plan against the
rules below and renders the stored note from validated JSON; running the plan is
:mod:`pkpipe.steps.session_plan`.

Rules (``plan_errors``)

* inputs are ``item.extract`` (the validated secure-fetch extract), ``item.text`` (the INBOX note text, raw),
  other ``item.<field>`` values, ``*`` (extract and every earlier session output) or the id of an earlier
  session;
* constitution IV: a session with tools never reads raw text (``item.text``, ``item``, ``*``), and only the
  output of a session that has an ``output_schema`` (validated JSON);
* FR-002: a session with the enterprise context has no tool on an ``egress`` server (web search), and no such
  session reads anything derived from the enterprise context;
* FR-003: an instruction is about 800 tokens at most (a quarter of its characters);
* the note is rendered from one session's output (``note_session``, default ``synthesize``, else the last
  session); a session with an ``output_schema`` gives one section per field, one without gives its text.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pkpipe.domain.layout import DEFAULT_LAYOUT
from pkpipe.domain.text_safety import neutralize_text

#: Inputs that are raw, unvalidated text: a session with tools must never receive them (constitution IV).
RAW_INPUTS = frozenset({"*", "item", "item.text", "item.body", "item.content", "item.raw"})
EXTRACT_REF = "item.extract"
#: Spec 023: the owner's comment on the item, a declared input (never part of ``*`` or ``item``): owner
#: authored, so trusted as intent, but offered as datamarked data like every input.
OWNER_NOTE_REF = "item.owner_note"
TEXT_REF = "item.text"
ENTERPRISE = "enterprise"
#: FR-003: an instruction is about this many tokens at most (estimated as a quarter of its characters).
INSTRUCTION_TOKEN_LIMIT = 800
DEFAULT_NOTE_SESSION = "synthesize"
#: Keys of a session output that propose a new category (the misc evaluation): see :func:`suggestion_of`.
SUGGESTION_KEY = "suggestion"


def estimate_tokens(text: str) -> int:
    return -(-len(text) // 4)


@dataclass(frozen=True)
class PlanInfo:
    """What the flow needs to know before it runs a plan."""

    sessions: tuple[str, ...]
    needs_fetch: bool  # a session reads the web page's extract: secure fetch runs first
    uses_text: bool  # a session reads the INBOX note's text
    note_session: str | None


def note_session_id(category: Mapping[str, Any]) -> str | None:
    """The session whose output becomes the note: ``note_session``, else ``synthesize``, else the last."""
    ids = [s["id"] for s in category.get("sessions") or []]
    explicit = category.get("note_session")
    if explicit:
        return explicit
    if DEFAULT_NOTE_SESSION in ids:
        return DEFAULT_NOTE_SESSION
    return ids[-1] if ids else None


def plan_info(category: Mapping[str, Any]) -> PlanInfo:
    sessions = category.get("sessions") or []
    refs = [r for s in sessions for r in s.get("inputs") or []]
    return PlanInfo(
        sessions=tuple(s["id"] for s in sessions),
        needs_fetch=any(r in (EXTRACT_REF, "*") for r in refs),
        uses_text=any(r in (TEXT_REF, "item") for r in refs),
        note_session=note_session_id(category),
    )


def input_error(ref: str, earlier: set[str]) -> str | None:
    """Why ``ref`` is not a valid input of a session whose predecessors are ``earlier``, or None."""
    if ref == "*" or ref in earlier:
        return None
    if ref == "item":
        return None
    if ref.startswith("item."):
        return None if re.fullmatch(r"item\.[a-z_][a-z0-9_]*", ref) else f"bad reference '{ref}'"
    return f"input '{ref}' is neither item.*, '*' nor an earlier session"


def _egress_tools(session: Mapping[str, Any], servers: Mapping[str, Any]) -> list[str]:
    return [t for t in session.get("tools") or [] if (servers.get(t.partition(":")[0]) or {}).get("egress")]


def plan_errors(
    category: Mapping[str, Any],
    *,
    servers: Mapping[str, Any],
    asset: Callable[[str], str | None],
    force: bool = False,
) -> tuple[list[str], list[str]]:
    """Errors and warnings of one category's session plan (see the module docstring).

    A category without sessions is a warning. Only a category that runs the plan (subflow
    ``other_resource``, or ``force``) is checked: the sessions of other subflows (the learning notebook
    agent) are descriptive."""
    cid = category.get("id", "?")
    if not force and category.get("subflow") != "other_resource":
        return [], []
    sessions = list(category.get("sessions") or [])
    errors: list[str] = []
    warnings: list[str] = []
    if not sessions:
        if category.get("subflow") == "other_resource":
            warnings.append(
                f"category '{cid}': subflow other_resource without a session plan (sessions): its items fail"
            )
        return errors, warnings
    schemas = {s["id"]: bool(s.get("output_schema")) for s in sessions}
    earlier: set[str] = set()
    tainted: set[str] = set()  # sessions whose output derives from the enterprise context
    for s in sessions:
        where = f"category '{cid}' session '{s['id']}'"
        inputs = list(s.get("inputs") or [])
        tools = list(s.get("tools") or [])
        for ref in inputs:
            msg = input_error(ref, earlier)
            if msg:
                errors.append(f"{where}: {msg}")
            if tools and ref in RAW_INPUTS:
                errors.append(
                    f"{where}: a session with tools must not receive raw untrusted input '{ref}' "
                    "(constitution IV: pass a validated extract or a session output instead)"
                )
            elif tools and ref in earlier and not schemas[ref]:
                errors.append(
                    f"{where}: a session with tools reads '{ref}', which has no output_schema "
                    "(constitution IV: only validated JSON reaches a session with tools)"
                )
        derived = {r for r in inputs if r in tainted} | (tainted if "*" in inputs else set())
        egress = _egress_tools(s, servers)
        if egress and s.get("context") == ENTERPRISE:
            errors.append(
                f"{where}: a session with the enterprise context must not have web search tools {egress} "
                "(spec 010 FR-002)"
            )
        if egress and derived:
            errors.append(
                f"{where}: web search tools {egress} on a session that reads enterprise-derived "
                f"{sorted(derived)} (spec 010 FR-002)"
            )
        if s.get("context") == ENTERPRISE or derived:
            tainted.add(s["id"])
        text = asset(f"instructions/{s['instruction']}")
        if text is not None and estimate_tokens(text) > INSTRUCTION_TOKEN_LIMIT:
            errors.append(
                f"{where}: instruction '{s['instruction']}' is about {estimate_tokens(text)} tokens "
                f"(FR-003: {INSTRUCTION_TOKEN_LIMIT} at most)"
            )
        earlier.add(s["id"])
    note = note_session_id(category)
    if note not in schemas:
        errors.append(f"category '{cid}': note_session '{note}' is not a session of the plan")
    return errors, warnings


# -- note rendering (code decides what is stored) -------------------------------------------------------
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value: Any) -> str:
    return _CONTROL.sub("", str(value)).strip()


def slugify(text: str, limit: int = 60) -> str:
    folded = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")[:limit].strip("-")
    return slug or "note"


def _heading(key: str) -> str:
    return key.replace("_", " ").strip().capitalize()


def _inline(value: Any) -> str:
    if isinstance(value, Mapping):
        return "; ".join(f"{k}: {_inline(v)}" for k, v in value.items() if v not in (None, "", [], {}))
    if isinstance(value, list | tuple):
        return ", ".join(_inline(v) for v in value)
    return clean_text(value)


def _block(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return [f"- **{_heading(k)}:** {_inline(v)}" for k, v in value.items() if v not in (None, "", [], {})]
    if isinstance(value, list | tuple):
        return [f"- {_inline(v)}" for v in value if v not in (None, "", [], {})]
    return [clean_text(value)] if clean_text(value) else []


def note_title(output: Mapping[str, Any], fallback: str) -> str:
    title = clean_text(output.get("title") or "")
    return title.splitlines()[0][:120] if title else fallback


def render_note_body(output: Mapping[str, Any], *, source: str | None = None) -> str:
    """Markdown of a session output: one section per field in schema order (the plain ``note`` and ``text``
    fields without a heading), ``title`` as the page title, empty and null fields left out."""
    lines = [f"# {note_title(output, 'Note')}", ""]
    if source:
        lines += [f"Source: {source}", ""]
    for key, value in output.items():
        if key in ("title", "tool_calls") or value in (None, "", [], {}) or isinstance(value, bool):
            continue
        block = _block(value)
        if not block:
            continue
        if key not in ("note", "text"):
            lines += [f"## {_heading(key)}", ""]
        lines += [*block, ""]
    return "\n".join(lines).rstrip() + "\n"


def note_path(para_path: str, title: str, item_id: str) -> str:
    """``<para path>/<slug of the title>-<item id>.md``: readable, unique per item, stable on a rerun."""
    return f"{para_path.rstrip('/')}/{slugify(title)}-{item_id}.md"


def suggestion_of(outputs: Mapping[str, Any], known_ids: set[str]) -> dict[str, str] | None:
    """A new-category suggestion (``suggestion: {name, triage_hint}``) of any session output that is a valid
    name, not an existing category id. Code decides whether it becomes an agent task (constitution V)."""
    for output in outputs.values():
        raw = output.get(SUGGESTION_KEY) if isinstance(output, Mapping) else None
        if not isinstance(raw, Mapping):
            continue
        name = (
            clean_text(raw.get("name") or "").splitlines()[0][:60]
            if clean_text(raw.get("name") or "")
            else ""
        )
        hint = " ".join(clean_text(raw.get("triage_hint") or "").split())[:200]
        cid = slugify(name)
        if len(name) < 2 or cid in known_ids or not hint:
            continue
        return {"id": cid, "name": name, "triage_hint": hint}
    return None


def agent_task_note(
    suggestion: Mapping[str, str], *, item_id: str, note: str, folder: str = DEFAULT_LAYOUT.agent_tasks_dir
) -> tuple[str, str, dict[str, Any]]:
    """(path, body, front matter) of the agent task that proposes a new category (below ``folder``, the
    layout's ``agent_tasks_dir``)."""
    path = f"{folder.rstrip('/')}/new-category-{suggestion['id']}-{item_id}.md"
    body = (
        f"# New category suggestion: {suggestion['name']}\n\n"
        f"- [ ] Decide whether to add the category `{suggestion['id']}` (an entry in `categories.json`).\n\n"
        f"Triage hint: {suggestion['triage_hint']}\n\n"
        f"Suggested by the evaluation of item {item_id} (note: {note}).\n"
    )
    front = {
        "type": "agent_task",
        "kind": "new_category",
        "status": "proposed",
        "category_id": suggestion["id"],
        "pkpipe_item": item_id,
    }
    return path, body, front


# -- a new-category proposal (spec 022) --------------------------------------------------------------
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PROPOSAL_STATE = "awaiting_category"


def proposal_path(folder: str, requested: str, item_id: str) -> str:
    """Where the proposal note of a new category for one item is written (below ``layout.proposals_dir``)."""
    return f"{folder.rstrip('/')}/new-category-{requested}-{item_id}.md"


def proposal_note(
    requested: str,
    output: Mapping[str, Any],
    *,
    item_id: str,
    folder: str = DEFAULT_LAYOUT.proposals_dir,
) -> tuple[str, str, dict[str, Any]]:
    """(path, body, front matter) of the note that proposes the category ``requested`` (a slug the owner
    asked for in an override). The pipeline never edits the versioned config: the note holds the evaluation
    of the item and a ready-to-paste ``categories.json`` entry; the owner adds it through a config draft
    (validate, eval, publish). The evaluation text comes from a model over untrusted input: it is neutralised
    before it is written (no markdown, HTML, wikilink or URL). Raises ``ValueError`` unless ``requested`` is
    a plain slug of 2 to 40 characters: it names the file."""
    if not _SLUG.match(requested) or not 2 <= len(requested) <= 40:
        raise ValueError("a new category is named by a slug (letters, digits, hyphens, 2 to 40 characters)")
    label = requested.replace("-", " ").capitalize()
    title = neutralize_text(output.get("title") or f"Item {item_id}", 120)
    note = neutralize_text(output.get("note") or "", 1000)
    suggestion = output.get("suggestion")
    hint = (
        neutralize_text(suggestion.get("triage_hint") or "", 200) if isinstance(suggestion, Mapping) else ""
    )
    hint = hint or f"Items the owner files under {label}."
    entry = {
        "id": requested,
        "label": label,
        "subflow": "other_resource",
        "para_path": "{resources_dir}/" + label.replace(" ", "-"),
        "triage_hint": hint,
        "sessions": [
            {
                "id": "summarize",
                "system": "synthesizer",
                "instruction": "other/summarize.md",
                "inputs": ["item.text"],
                "output_schema": "resource_note",
            }
        ],
    }
    body = (
        f"# New category proposal: {label}\n\n"
        f"- [ ] Decide whether to add the category `{requested}`.\n\n"
        f"The owner wrote `{requested}` as the override of item {item_id}, and no category with that name is "
        "configured. The pipeline never edits the configuration: the item waits (state "
        f"`{PROPOSAL_STATE}`) and nothing was filed.\n\n"
        f"## Evaluation of the item\n\nTitle: {title}\n\n{note}\n\n"
        "## Draft entry for categories.json\n\n```json\n"
        f"{json.dumps(entry, indent=2, ensure_ascii=False)}\n```\n\n"
        "## How to add it\n\n"
        "1. Open a config draft (the UI), add the entry above to the overlay `categories.json`, add an "
        "instruction file and a session of your own if the generic `summarize` session is not enough.\n"
        "2. Validate the draft, run the evals, publish it.\n"
        "3. The item is released by the next 06:00 run when its override names the configured category; "
        "you can also change the override to an existing category at any time.\n"
    )
    front = {
        "type": "agent_task",
        "kind": "new_category",
        "status": "proposed",
        "category_id": requested,
        "pkpipe_item": item_id,
    }
    return proposal_path(folder, requested, item_id), body, front
