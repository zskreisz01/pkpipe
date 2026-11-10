"""Code functions that put results into the vault (spec 032, BPMN: Store in Obsidian / Get audio + link).

* ``learning.audio_plan`` / ``learning.note``: the learning note of a ready notebook (facts of our own only)
  and the optional copy of the podcast into the vault (``learning.copy_audio_to_vault``).
* ``image.result_note``: the note of an image item whose address was not found, from its validated
  description. The description derives from untrusted image content, so the flow runs the output guard over
  this note.
* ``results.write_indexes``: refreshes the ``_Index.md`` of every result folder after a release run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from pkpipe.domain import results
from pkpipe.steps.base import StepContext, StepResult, layout_of, register_code
from pkpipe.steps.session_plan import categories_of
from pkpipe.steps.tool import call_tool


def _day(ctx: StepContext) -> str:
    return datetime.fromtimestamp(ctx.ports.clock.now(), UTC).strftime("%Y-%m-%d")


def _copy_audio(ctx: StepContext) -> bool:
    return bool(((ctx.snapshot.data or {}).get("learning") or {}).get("copy_audio_to_vault"))


@register_code("learning.audio_plan")
def audio_plan(ctx: StepContext, audio: dict[str, Any] | None = None) -> dict[str, Any]:
    """Whether and how the podcast is copied into the vault: ``{copy, src, name}`` (``name`` is unique per
    item). ``copy`` is false unless ``learning.copy_audio_to_vault`` is on and the gateway gave a file."""
    path = str(ctx.resolve("audio.audio_path", required=False) or (audio or {}).get("audio_path") or "")
    item_id = str((ctx.item or {}).get("id") or "")
    if not (_copy_audio(ctx) and path and item_id):
        return {"copy": False, "src": path, "name": ""}
    return {"copy": True, "src": path, "name": results.audio_vault_name(item_id, PurePosixPath(path).name)}


@register_code("learning.note")
def learning_note(
    ctx: StepContext,
    prepare: str = "prepare",
    link: str = "link",
    audio: str = "audio",
    plan: str = "audio_plan",
    imported: str = "import_audio",
) -> dict[str, Any]:
    """The learning note: ``{path, title, body, front_matter}``. Arguments name the nodes (the import node
    may not have run or may have failed: then the podcast is only named by its path)."""
    item = ctx.item or {}
    prep = ctx.resolve(prepare, required=False) or {}
    lnk = ctx.resolve(link, required=False) or {}
    aud = ctx.resolve(audio, required=False) or {}
    pln = ctx.resolve(plan, required=False) or {}
    imp = ctx.resolve(imported, required=False)
    copied = isinstance(imp, dict) and imp.get("path") and pln.get("name")
    ui = ((ctx.snapshot.data or {}).get("ui") or {}).get("public_url")
    note = results.learning_note(
        item_id=str(item.get("id") or ""),
        category=str(item.get("category") or ""),
        para_path=str(item.get("para_path") or layout_of(ctx).resources_dir + "/Learning"),
        source=str(prep.get("url") or item.get("source_url") or item.get("resource") or ""),
        notebook_url=str(lnk.get("url") or ""),
        audio_file=str(aud.get("audio_path") or "") or None,
        audio_note=str(pln["name"]) if copied else None,
        ui_url=ui,
        created=_day(ctx),
    )
    ctx.event("result", "written", what="learning_note", path=note["path"])
    return note


@register_code("image.result_note")
def image_result_note(ctx: StepContext) -> StepResult:
    """The note of an image item without an address: ``status`` ``ok`` with the note, or ``none`` when the
    item has no usable description (the item then fails as before). The note goes through the output guard."""
    item = ctx.item or {}
    description = item.get("description")
    if not isinstance(description, dict) or not item.get("para_path"):
        return StepResult(output={"status": "none"})
    note = results.image_note(
        item_id=str(item.get("id") or ""),
        category=str(item.get("category") or ""),
        para_path=str(item["para_path"]),
        resource=str(item.get("resource") or ""),
        description=description,
        created=_day(ctx),
    )
    if note is None:
        return StepResult(output={"status": "none"})
    inbox = PurePosixPath(item.get("path") or item.get("resource") or f"{item['id']}.md").name
    note["archive_path"] = f"{layout_of(ctx).archive_dir}/{item['id']}-{inbox}"
    note["status"] = "ok"
    ctx.event("result", "written", what="image_note", path=note["path"])
    return StepResult(output=note)


def result_folders(ctx: StepContext) -> list[str]:
    """The folders of the enabled categories (their ``para_path``), once each."""
    layout = layout_of(ctx)
    seen: list[str] = []
    for cat in categories_of(ctx).values():
        path = cat.get("para_path")
        if path:
            folder = layout.expand(str(path)).rstrip("/")
            if folder and folder not in seen:
                seen.append(folder)
    return seen


@register_code("results.write_indexes")
def write_indexes(ctx: StepContext) -> dict[str, Any]:
    """Refresh ``_Index.md`` of every result folder through the vault tool. A folder the vault policy does not
    allow, or any other error of one folder, is counted and never stops the others or the run."""
    written = failed = notes = 0
    for folder in result_folders(ctx):
        try:
            out = call_tool(ctx, "obsidian:write_results_index", {"folder": folder})
        except Exception:  # noqa: BLE001 - one folder must not stop the others
            failed += 1
            continue
        if isinstance(out, dict):
            written += 1 if out.get("written") else 0
            notes += int(out.get("notes") or 0)
    return {"written": written, "failed": failed, "notes": notes}
