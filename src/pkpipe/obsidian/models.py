"""Row models of the triage file and the processing list (spec 004 data-model.md)."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from pkpipe.domain.improvement import ImprovementItem
from pkpipe.domain.text_safety import neutralize_text
from pkpipe.domain.tool_candidate import ToolCandidate
from pkpipe.domain.triage import ItemState, item_id_for

__all__ = ["ItemState", "ProcessingListRow", "ProcessingStatus", "TriageRow", "item_id_for"]


class ProcessingStatus(StrEnum):
    UNPROCESSED = "unprocessed"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    DONE = "done"
    MANUAL = "manual"


#: ``![[target]]`` or ``![[target|width]]``: the embed form of an image row's resource cell.
EMBED_CELL = re.compile(r"^!\[\[([^\]|#]+)(?:\|(\d+))?\]\]$")
ROW_KINDS = ("note", "link", "image", "text_link", "text")
TOPIC_CHARS = 200


class TriageRow(BaseModel):
    item_id: str
    resource: str
    ai_suggested_category: str
    confirm: bool = False
    override: str | None = None
    state: ItemState = ItemState.SUGGESTED
    #: ``note`` or the entry kind of the list file (``link``, ``image``, ``text_link``, ``text``; spec 020)
    kind: str = "note"
    #: The category is the owner's decision: an image that could not be described or was flagged (spec 021)
    needs_owner: bool = False
    #: One line about an image (spec 021): its topic, or why it was not described. The full description stays
    #: in the app DB; the visible text of an image never lands in the vault.
    topic: str = ""
    #: Spec 021: an image row shows its image in Obsidian: the resource cell is the local embed
    #: ``![[name|width]]`` (an escaped pipe inside the table). ``None``/0: the plain name. It is part of how
    #: the row is written, not of its identity: the item id stays the hash of the resource (the listed name).
    embed_width: int | None = Field(default=None, ge=0, le=4000)

    @field_validator("embed_width")
    @classmethod
    def _no_zero_width(cls, v: int | None) -> int | None:
        return v or None

    @field_validator("resource", mode="before")
    @classmethod
    def _unwrap_embed(cls, v: object) -> object:
        """A resource given as an embed (``![[name|200]]``) is the name inside it."""
        if isinstance(v, str):
            m = EMBED_CELL.match(v.strip())
            if m:
                return m.group(1).strip()
        return v

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "note"
        if isinstance(v, str) and v.strip().lower() not in ROW_KINDS:
            raise ValueError(f"unknown kind {v!r}")
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("needs_owner", mode="before")
    @classmethod
    def _needs_owner(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower() in ("yes", "true", "x", "[x]", "1")
        return v

    @field_validator("topic", mode="before")
    @classmethod
    def _topic(cls, v: object) -> object:
        if v is None:
            return ""
        return neutralize_text(v, TOPIC_CHARS) if isinstance(v, str) else v

    @field_validator("item_id", "resource", "ai_suggested_category")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("override", mode="before")
    @classmethod
    def _blank_override(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    def to_cells(self) -> dict[str, str]:
        return {
            "confirm": "[x]" if self.confirm else "[ ]",
            "item": self.item_id,
            "resource": self.resource_cell(),
            "suggested": self.ai_suggested_category,
            "override": self.override or "",
            "state": self.state.value,
            "kind": "" if self.kind == "note" else self.kind,
            "needs_owner": "yes" if self.needs_owner else "",
            "topic": self.topic,
        }

    def resource_cell(self) -> str:
        """The resource as written in the table: the local embed of an image row (never a remote URL), else
        the plain resource."""
        if (
            self.kind == "image"
            and self.embed_width
            and not self.resource.startswith(("http://", "https://"))
        ):
            return f"![[{self.resource}|{self.embed_width}]]"
        return self.resource

    @classmethod
    def from_cells(cls, cells: dict[str, str]) -> TriageRow:
        mark = cells["confirm"].strip().lower()
        if mark not in ("[ ]", "[x]", ""):
            raise ValueError(f"bad checkbox {cells['confirm']!r}")
        embed = EMBED_CELL.match(cells["resource"].strip())
        return cls(
            embed_width=int(embed.group(2)) if embed and embed.group(2) else None,
            item_id=cells["item"],
            resource=cells["resource"],
            ai_suggested_category=cells["suggested"],
            confirm=mark == "[x]",
            override=cells["override"],
            state=cells["state"].strip().lower() or ItemState.SUGGESTED,
            kind=cells.get("kind", ""),
            needs_owner=cells.get("needs_owner", ""),
            topic=cells.get("topic", ""),
        )


class ProcessingListRow(BaseModel):
    item_id: str
    category: str
    obsidian_link: str
    nb_id: str | None = None
    link: str | None = None
    status: ProcessingStatus = ProcessingStatus.UNPROCESSED

    @field_validator("item_id", "category", "obsidian_link")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("nb_id", "link", mode="before")
    @classmethod
    def _blank(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    def to_cells(self) -> dict[str, str]:
        return {
            "item": self.item_id,
            "category": self.category,
            "note": self.obsidian_link,
            "nb_id": self.nb_id or "",
            "link": self.link or "",
            "status": self.status.value,
        }

    @classmethod
    def from_cells(cls, cells: dict[str, str]) -> ProcessingListRow:
        return cls(
            item_id=cells["item"],
            category=cells["category"],
            obsidian_link=cells["note"],
            nb_id=cells["nb_id"],
            link=cells["link"],
            status=cells["status"].strip().lower() or ProcessingStatus.UNPROCESSED,
        )


def vault_json_schemas() -> dict[str, dict]:
    """JSON Schemas of the triage and processing-list rows (spec 004 data-model.md), keyed by file stem."""
    return {
        "triage-row": TriageRow.model_json_schema(),
        "processing-list-row": ProcessingListRow.model_json_schema(),
        "improvement-item": ImprovementItem.model_json_schema(),
        "tool-candidate": ToolCandidate.model_json_schema(),
    }
