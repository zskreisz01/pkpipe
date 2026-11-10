"""Result notes and result indexes of the vault (spec 032). Pure logic: strings and dicts in, text out.

* A *learning note* is written when a notebook (and its podcast) is ready. It holds only facts of our own:
  the source address, the notebook link, the podcast file, the history link. No text of the page is derived
  or stored (the learning flow's rule), so no output guard is needed.
* An *image note* is the result of an image item that names no address and was not searched for: the
  validated description of the image (topic, visible text, addresses) in the category's folder. The
  description derives from untrusted image content, so the flow runs the output guard over this note first.
* A *results index* (``_Index.md`` in a result folder) lists every note of pkpipe in that folder, so the notes
  are findable without a plugin. The file is machine-owned and rewritten only when its text changes.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from pkpipe.domain import in_progress
from pkpipe.domain.session_plan import clean_text, note_path, slugify

INDEX_NAME = "_Index.md"
NOTE_TYPES = ("resource_note", "learning_note")
MAX_VISIBLE = 400
MAX_ROWS = 500
AUDIO_EXTENSIONS = ("m4a", "mp3", "mp4", "wav", "ogg")
_ID_SUFFIX = re.compile(r"-([0-9a-f]{12})\.md$")


def host_and_path(url: str) -> str:
    """``bbycroft.net/llm`` for a title: readable, no scheme, query or fragment."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").removeprefix("www.")
    path = parts.path.strip("/")
    return f"{host}/{path}"[:80].rstrip("/") if host else ""


def learning_note(
    *,
    item_id: str,
    category: str,
    para_path: str,
    source: str,
    notebook_url: str,
    audio_file: str | None,
    audio_note: str | None,
    ui_url: str | None,
    created: str,
) -> dict[str, Any]:
    """The note of a ready notebook: ``{path, title, body, front_matter}``."""
    title = f"Learning: {host_and_path(source)}" if host_and_path(source) else "Learning note"
    lines = [f"# {title}", "", f"Source: {source}" if source else "Source: (none)", ""]
    lines += [f"Notebook: {notebook_url}" if notebook_url else "Notebook: (no link)", ""]
    if audio_note:
        lines += [f"Podcast: ![[{audio_note}]]", ""]
    elif audio_file:
        lines += [f"Podcast: `{audio_file}` (on the machine that runs pkpipe)", ""]
    else:
        lines += ["Podcast: not available", ""]
    if ui_url:
        lines += [f"History: {ui_url.rstrip('/')}/items/{item_id}", ""]
    front: dict[str, Any] = {
        "type": "learning_note",
        "source": source,
        "category": category,
        "pkpipe_item": item_id,
        "notebook_url": notebook_url,
        "created": created,
    }
    if audio_file:
        front["audio_file"] = audio_file
    return {
        "path": note_path(para_path, title, item_id),
        "title": title,
        "body": "\n".join(lines).rstrip() + "\n",
        "front_matter": front,
    }


def _quote(text: str) -> list[str]:
    return [f"> {line}" for line in (ln.strip() for ln in text.splitlines()) if line]


def usable_description(description: Mapping[str, Any] | None) -> bool:
    """A description says something: at least three letters or digits in the topic and the visible text."""
    if not isinstance(description, Mapping):
        return False
    text = f"{description.get('topic') or ''} {description.get('visible_text') or ''}"
    return len(re.sub(r"[\W_]+", "", text)) >= 3


def image_note(
    *,
    item_id: str,
    category: str,
    para_path: str,
    resource: str,
    description: Mapping[str, Any],
    created: str,
) -> dict[str, Any] | None:
    """The result note of an image item from its validated description; ``None`` when the description says
    nothing (no topic and no visible text)."""
    if not usable_description(description):
        return None
    topic = " ".join(clean_text(description.get("topic") or "").split())[:200]
    visible = clean_text(description.get("visible_text") or "")[:MAX_VISIBLE]
    urls = [str(u).strip() for u in (description.get("urls") or []) if str(u).strip()][:10]
    title = topic or f"Image {item_id}"
    lines = [f"# {title}", "", f"Image: `{resource}`", ""]
    lines += ["## What the image shows", "", topic or "(not described)", ""]
    if visible:
        lines += ["## Text in the image", "", *_quote(visible), ""]
    if urls:
        lines += ["## Addresses in the image", "", *[f"- {u}" for u in urls], ""]
    lang = " ".join(clean_text(description.get("language") or "").split())[:20]
    kind = " ".join(clean_text(description.get("kind") or "").split())[:20]
    meta = ", ".join(x for x in (f"kind: {kind}" if kind else "", f"language: {lang}" if lang else "") if x)
    if meta:
        lines += [f"_{meta}_", ""]
    lines += [
        "## Next step",
        "",
        "No page was found for this image, so the note holds what the image itself shows. Add the link to "
        "the entry in the INBOX and release the item again to have the page evaluated.",
        "",
    ]
    return {
        "path": note_path(para_path, title, item_id),
        "title": title,
        "body": "\n".join(lines),
        "front_matter": {
            "type": "resource_note",
            "source": "",
            "origin": "image_description",
            "category": category,
            "pkpipe_item": item_id,
            "inbox_note": resource,
            "created": created,
        },
    }


# -- results index ----------------------------------------------------------------------------------
def _section_line(body: str, heading: str) -> str:
    """The first non-empty line below ``## <heading>`` (a one-word verdict such as ``hold``)."""
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$", body, re.I | re.M)
    if not match:
        return ""
    for line in body[match.end() :].splitlines():
        if line.strip().startswith("#"):
            return ""
        if line.strip():
            return line.strip()
    return ""


def index_row(name: str, front: Mapping[str, Any], body: str, mtime: float) -> dict[str, str] | None:
    """One row for a note file of the folder; ``None`` for a note pkpipe did not write."""
    if front.get("type") not in NOTE_TYPES or not front.get("pkpipe_item"):
        return None
    title = next(
        (ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), name.removesuffix(".md")
    )
    created = str(front.get("created") or "")[:10] or datetime.fromtimestamp(mtime, UTC).strftime("%Y-%m-%d")
    note = "podcast + notebook" if front.get("notebook_url") else _section_line(body, "Recommendation")
    if front.get("origin") == "image_description":
        note = "from the image only"
    return {
        "date": created,
        "stem": name.removesuffix(".md"),
        "title": title,
        "category": str(front.get("category") or ""),
        "source": str(front.get("source") or ""),
        "note": note,
        "notebook": str(front.get("notebook_url") or ""),
        "item": str(front.get("pkpipe_item")),
    }


def render_index(folder: str, rows: list[dict[str, str]]) -> str:
    """The ``_Index.md`` of a result folder, newest first (at most :data:`MAX_ROWS` rows)."""
    ordered = sorted(rows, key=lambda r: (r["date"], r["stem"]), reverse=True)
    lines = [
        "---",
        "type: pkpipe_index",
        "---",
        f"# Index of {folder.rstrip('/').rsplit('/', 1)[-1]}",
        "",
        "Machine-owned list of the notes pkpipe wrote in this folder (newest first). pkpipe rewrites it "
        "after every release run when it changed; do not edit it by hand.",
        "",
        "| date | note | category | source | result | notebook | item |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in ordered[:MAX_ROWS]:
        host = (
            host_and_path(r["source"])
            or r["source"]
            or ("image" if r["note"] == "from the image only" else "")
        )
        title = in_progress.cell(r["title"], 90)
        lines.append(
            f"| {r['date']} | [[{r['stem']}\\|{title}]] | {in_progress.cell(r['category'], 30)} "
            f"| {in_progress.cell(host, 60)} | {in_progress.cell(r['note'], 40)} "
            f"| {in_progress.cell(r['notebook'], 120)} | `{r['item']}` |"
        )
    if len(ordered) > MAX_ROWS:
        lines += ["", f"(and {len(ordered) - MAX_ROWS} older notes, not listed)"]
    return "\n".join(lines) + "\n"


def item_id_of(filename: str) -> str | None:
    """The 12-character item id at the end of a pkpipe note's file name."""
    match = _ID_SUFFIX.search(filename)
    return match.group(1) if match else None


def audio_vault_name(item_id: str, source_name: str) -> str:
    """File name of a copied podcast: ``<item id>-<slug of the audio file stem>.<ext>``."""
    stem, dot, ext = source_name.rpartition(".")
    if not dot or ext.lower() not in AUDIO_EXTENSIONS:
        stem, ext = source_name, "m4a"
    return f"{item_id}-{slugify(stem, 40)}.{ext.lower()}"
