"""Deterministic ``code`` functions of the ``context_refresh`` flows (spec 011, BPMN: Refresh Project
Contexts).

The local sessions only summarize the project notes; these functions decide what is written (constitution V):
which notes are read, which names are private, what the pack says within its token budget, whether the owner's
edits are kept, and which terms the query guard blocks from now on. All of them handle private data, so a flow
that calls one must declare the data class ``context`` (validation: local model only, no egress tool, no trace
content capture) and every function checks that again when it runs.
"""

from __future__ import annotations

import re
import threading
from datetime import UTC, datetime
from typing import Any

from pkpipe.domain import context_pack as cp
from pkpipe.security import heuristics, query_guard
from pkpipe.steps.base import StepContext, StepError, StepResult, layout_of, register_code

DATA_CLASS = "context"
STORE_PORT = "context_store"
SCOPE_KINDS = ("personal", "enterprise")
ID_RE = re.compile(r"[a-z][a-z0-9_]*")
PACK_RE = re.compile(r"[a-z][a-z0-9_-]*")
#: The public system pack is maintained with the design and is never generated or overwritten.
RESERVED_PACKS = frozenset({"system"})
MAX_NOTES = 60  # what read_notes accepts
#: Scopes run in parallel and share the query guard's terms file: its read-merge-write is serialized, or one
#: scope's names would be lost (and its web queries no longer blocked).
_TERMS_LOCK = threading.Lock()


def _private(ctx: StepContext) -> None:
    """Defense in depth next to the validation rule: no context function runs in a flow that is not marked
    (and so not held to the local-only rules)."""
    if DATA_CLASS not in ctx.flow.data_classes:
        raise StepError(
            f"flow '{ctx.flow.id}' handles context packs and must declare data class '{DATA_CLASS}'"
        )


def _store(ctx: StepContext):
    try:
        return ctx.ports.require(STORE_PORT)
    except StepError:
        raise StepError(
            "context packs are written to the private config overlay: set PKPIPE_OVERLAY_DIR (or --overlay); "
            "the public pack is never written"
        ) from None


def _security(ctx: StepContext) -> dict[str, Any]:
    return ctx.snapshot.section("security", {}) or {}


def _terms_path(ctx: StepContext) -> str | None:
    path = (_security(ctx).get("query_guard") or {}).get("terms_file")
    return path if isinstance(path, str) and path.split("/", 1)[0] in ("security", "contexts") else None


def _configured_terms(ctx: StepContext) -> list[cp.Term]:
    """The owner's own blocked terms (config list and terms file): scrubbed from the pack as well."""
    guard = (_security(ctx).get("query_guard")) or {}
    text = None
    path = _terms_path(ctx)
    if path:
        store = ctx.ports.extra.get(STORE_PORT)
        text = (store.read(path) if store is not None else None) or ctx.snapshot.asset(path)
    return [cp.Term(t, "name") for t in query_guard.blocked_terms(guard, text)]


# -- scopes --------------------------------------------------------------------------------------------------
@register_code("context.scopes", data_class=DATA_CLASS)
def scopes(ctx: StepContext, scopes: list[dict[str, Any]]) -> dict[str, Any]:
    """The packs to refresh: ``{id, kind, folder, pack, max_tokens, max_notes, max_chars}`` per scope.
    ``kind`` ``personal`` or ``enterprise`` picks the session (enterprise = anonymized, terms for the query
    guard)."""
    _private(ctx)
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_packs: set[str] = set()
    for raw in scopes or []:
        scope = {
            "max_tokens": 1500,
            "max_notes": 40,
            "max_chars": 24000,
            **{k: v for k, v in dict(raw).items() if v is not None},
        }
        sid, kind, folder = str(scope.get("id", "")), scope.get("kind"), str(scope.get("folder", ""))
        pack = str(scope.get("pack") or sid)
        if not ID_RE.fullmatch(sid):
            raise StepError(f"scope id '{sid}' must match {ID_RE.pattern}")
        if kind not in SCOPE_KINDS:
            raise StepError(f"scope '{sid}': kind must be one of {', '.join(SCOPE_KINDS)}")
        if not PACK_RE.fullmatch(pack) or pack in RESERVED_PACKS:
            raise StepError(f"scope '{sid}': pack '{pack}' is not a valid name (system is reserved)")
        parts = folder.split("/")
        if not folder or folder.startswith("/") or any(p in ("", ".", "..") for p in parts):
            raise StepError(f"scope '{sid}': folder '{folder}' must be a relative vault folder")
        if sid in seen_ids or pack in seen_packs:
            raise StepError(f"scope '{sid}': id and pack must be unique")
        for key in ("max_tokens", "max_notes", "max_chars"):
            if not isinstance(scope[key], int) or scope[key] < 1:
                raise StepError(f"scope '{sid}': {key} must be a positive integer")
        scope["max_notes"] = min(scope["max_notes"], MAX_NOTES)
        seen_ids.add(sid)
        seen_packs.add(pack)
        out.append({**scope, "id": sid, "pack": pack, "folder": folder})
    return {"scopes": out, "count": len(out)}


# -- reading -------------------------------------------------------------------------------------------------
@register_code("context.select", data_class=DATA_CLASS)
def select(ctx: StepContext, listing: dict[str, Any], folder: str, max_notes: int = 40) -> dict[str, Any]:
    """The project notes of a folder listing (``list_folder`` result): ``.md`` notes, sorted, at most
    ``max_notes``. Sync conflict copies are not in the listing; subfolders are not read."""
    _private(ctx)
    names = sorted(str(n) for n in (listing or {}).get("notes", []) if str(n).endswith(".md"))
    paths = [f"{folder.rstrip('/')}/{n}" for n in names][: min(int(max_notes), MAX_NOTES)]
    return {"paths": paths, "count": len(paths), "listed": len(names), "folder": folder}


def _scalar_front_matter(front_matter: dict[str, Any]) -> str:
    parts = []
    for key, value in front_matter.items():
        if isinstance(value, str | int | float | bool):
            parts.append(f"{key}: {cp.clip(str(value), 120)}")
        elif isinstance(value, list):
            parts.append(f"{key}: {cp.clip(', '.join(str(v) for v in value), 160)}")
    return "; ".join(parts)


def _excluded(front_matter: dict[str, Any]) -> bool:
    value = (front_matter or {}).get("context_exclude")
    return value is True or str(value).strip().lower() in ("true", "yes", "1")


@register_code("context.prepare", data_class=DATA_CLASS)
def prepare(
    ctx: StepContext,
    notes: dict[str, Any],
    kind: str,
    max_chars: int = 24000,
    name_fields: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The text the summarizing session reads (title, front matter and body of each note, within
    ``max_chars``) and, for the enterprise kind, the names the notes declare themselves (front matter,
    titles)."""
    _private(ctx)
    listed = [n for n in (notes or {}).get("notes", []) if isinstance(n, dict)]
    used = [n for n in listed if not _excluded(n.get("front_matter") or {})]
    per_note = max(600, int(max_chars) // max(len(used), 1))
    blocks, budget, truncated = [], int(max_chars), False
    for note in used:
        stem = str(note.get("path", "")).rsplit("/", 1)[-1].removesuffix(".md")
        meta = _scalar_front_matter(note.get("front_matter") or {})
        body = str(note.get("body") or "").strip()
        if len(body) > per_note:
            body, truncated = body[:per_note].rsplit(" ", 1)[0] + " …", True
        block = f"## {stem}\n" + (f"Front matter: {meta}\n" if meta else "") + body
        if len(block) > budget:
            truncated = True
            break
        blocks.append(block)
        budget -= len(block) + 2
    terms = cp.unique_terms(cp.harvest_terms(used, name_fields)) if kind == "enterprise" else []
    return {
        "count": len(blocks),
        "text": "\n\n".join(blocks),
        "excluded": len(listed) - len(used),
        "truncated": truncated,
        "terms": [{"term": t.term, "kind": t.kind} for t in terms],
    }


@register_code("context.no_notes", data_class=DATA_CLASS)
def no_notes(ctx: StepContext, folder: str = "", listed_by: str = "list") -> dict[str, Any]:
    """Nothing to summarize. A folder that does not exist (or has no project note) yields the empty pack
    with a warning (``listed_by`` names the listing node); any other listing failure (the vault server is
    down) fails the scope instead, so a transient error never blanks a good pack."""
    _private(ctx)
    error = ctx.resolve("error", required=False)
    if isinstance(error, dict) and error.get("node") == listed_by:
        if "not found" not in str(error.get("message", "")).lower():
            raise StepError(f"could not list '{folder}': {error.get('type', 'error')}")
    return {"count": 0, "folder": folder}


# -- assembling ----------------------------------------------------------------------------------------------
def _heuristic_flags(ctx: StepContext, text: str) -> list[str]:
    threshold = float((_security(ctx).get("heuristics") or {}).get("threshold", 1.0))
    found = heuristics.scan(text)
    return list(found.matches) if found.score >= threshold else []


@register_code("context.assemble", data_class=DATA_CLASS)
def assemble(
    ctx: StepContext,
    kind: str,
    pack: str,
    max_tokens: int = 1500,
    summary: dict[str, Any] | None = None,
    prepared: dict[str, Any] | None = None,
) -> StepResult:
    """The generated part of a pack from the session's summary. Enterprise: every private name (front matter,
    titles, names the session reported that occur in the notes, the owner's blocked terms) is scrubbed and the
    result verified with the query guard's own matching; a line that still matches is dropped. A summary that
    trips the injection heuristics is not written. ``summary`` ``None`` renders the empty pack."""
    _private(ctx)
    prepared = prepared or {}
    terms: list[cp.Term] = []
    known: list[cp.Term] = []
    if kind == "enterprise":
        harvested = [cp.Term(t["term"], t.get("kind", "name")) for t in prepared.get("terms", [])]
        reported = cp.session_terms((summary or {}).get("names"), str(prepared.get("text", "")))
        terms = cp.unique_terms(harvested, reported)
        known = cp.unique_terms(terms, _configured_terms(ctx))
    rendered = cp.render_body(kind, summary, max_tokens=int(max_tokens), terms=known)
    if kind == "enterprise" and known and cp.leaks(rendered.body, known):
        raise StepError("a private name is still in the enterprise pack: nothing written")
    flags = _heuristic_flags(ctx, rendered.body)
    if flags:
        raise StepError(
            f"the generated {kind} pack trips the injection heuristics ({', '.join(flags)}): nothing written"
        )
    warnings = []
    if summary is None:
        warnings.append("no project notes found: the pack is empty")
    elif not rendered.projects:
        warnings.append("the summary names no project")
    if rendered.trimmed:
        warnings.append(f"{rendered.trimmed} project(s) left out to stay within {max_tokens} tokens")
    if rendered.dropped:
        warnings.append(f"{rendered.dropped} line(s) dropped: a private name survived the scrub")
    if prepared.get("truncated"):
        warnings.append("the notes were longer than the read budget and were cut")
    out = {
        "name": pack,
        "kind": kind,
        "body": rendered.body,
        "tokens": rendered.tokens,
        "projects": rendered.projects,
        "scrubbed": rendered.scrubbed,
        "trimmed": rendered.trimmed,
        "dropped": rendered.dropped,
        # the terms file gets every form (full name, short form, last name): a web query is matched on whole
        # words, so "Brightwater" must be listed itself to block "brightwater case study"
        "terms": [{"term": v, "kind": kind} for v, kind in cp.all_variants(terms)],
        "warnings": warnings,
        "empty": summary is None,
    }
    return StepResult(
        output={k: v for k, v in out.items() if k not in ("body", "terms")}, item={"built": out}
    )


# -- writing -------------------------------------------------------------------------------------------------
@register_code("context.write", data_class=DATA_CLASS)
def write(ctx: StepContext, pack: dict[str, Any], review_folder: str | None = None) -> StepResult:
    """Store the pack in the overlay (the decision is made against the file as it is now): a new or unedited
    pack is replaced, a pack the owner edited is left alone and the new version goes to
    ``<pack>.md.proposed``; text below the manual marker is kept. The enterprise terms are appended to the
    query guard's terms file first, so they block web queries even if writing the pack fails. Returns the
    status and the review note to write."""
    _private(ctx)
    store = _store(ctx)
    name = str(pack["name"])
    path = f"contexts/{name}.md"
    terms = [cp.Term(t["term"], t.get("kind", "name")) for t in pack.get("terms") or []]
    added: list[str] = []
    terms_path = _terms_path(ctx)
    warnings = list(pack.get("warnings") or [])
    if terms and terms_path is None:
        warnings.append(
            "security.query_guard.terms_file is not set: the names are not stored for the query guard"
        )
    elif terms and terms_path:
        day = datetime.fromtimestamp(ctx.ports.clock.now(), UTC).date().isoformat()
        with _TERMS_LOCK:
            current = store.read(terms_path)
            text, added = cp.merge_terms_file(
                current if current is not None else ctx.snapshot.asset(terms_path), terms, day
            )
            if added:
                store.write(terms_path, text)
    result = cp.merge_pack(store.read(path), str(pack["body"]))
    proposed_path = path + cp.PROPOSED_SUFFIX
    if result.status in ("created", "updated"):
        store.write(path, result.text or "")
        store.remove(proposed_path)
    elif result.status == "proposed":
        store.write(proposed_path, result.proposal or "")
    else:
        store.remove(proposed_path)
    review_path = review_body = None
    if result.changed:
        review_path = f"{(review_folder or layout_of(ctx).context_review_dir).rstrip('/')}/{name}.md"
        review_body = cp.review_note(name, result.status, result.diff)
    slim = {
        "name": name,
        "kind": pack.get("kind"),
        "status": result.status,
        "tokens": pack.get("tokens"),
        "projects": pack.get("projects"),
        "terms_added": len(added),
        "warnings": warnings,
        "empty": bool(pack.get("empty")),
        "review_path": review_path,
        "review_body": review_body,
    }
    return StepResult(output={k: v for k, v in slim.items() if k != "review_body"}, item={"built": slim})


# -- the owner's notification --------------------------------------------------------------------------------
_PHRASE = {
    "created": "created",
    "updated": "updated",
    "unchanged": "unchanged",
    "proposed": "proposed (you edited it, so it was left as it is)",
}


@register_code("context.summary", data_class=DATA_CLASS)
def summary(ctx: StepContext, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts and where to look; never the pack text, a diff or a name. A failed scope is reported by its
    error type only: the message of a failed session or tool can quote the notes (a schema violation quotes
    the offending value) and the notification leaves the machine; the full error stays in the local run."""
    _private(ctx)
    parts: list[str] = []
    warnings: list[str] = []
    review: list[str] = []
    changed = failed = 0
    no_overlay = STORE_PORT not in ctx.ports.extra
    for r in results or []:
        item = r.get("item") or {}
        pack = item.get("built") if isinstance(item.get("built"), dict) else None
        scope = str(item.get("id", "?"))
        if r.get("status") != "done" or pack is None:
            failed += 1
            err = r.get("error") or {}
            err_type = re.sub(r"[^A-Za-z0-9_.]", "", str(err.get("type") or "error"))[:60] or "error"
            parts.append(f"{scope}: failed ({err_type}, details in run {ctx.run.run_id})")
            continue
        status = pack["status"]
        changed += status != "unchanged"
        parts.append(f"{pack['name']}: {_PHRASE.get(status, status)}")
        warnings += [f"{pack['name']}: {w}" for w in pack.get("warnings") or []]
        if pack.get("review_path"):
            review.append(pack["review_path"])
    total = changed + failed + len(warnings)
    message = ""
    if total:
        message = "Context packs refreshed: " + "; ".join(parts) + "."
        if warnings:
            message += " Warnings: " + "; ".join(warnings) + "."
        if review:
            message += " Review the diff in " + ", ".join(review) + "."
        if failed and no_overlay:
            message += " Context packs are written to the private config overlay: set PKPIPE_OVERLAY_DIR."
    return {"total": total, "changed": changed, "failed": failed, "warnings": warnings, "message": message}
