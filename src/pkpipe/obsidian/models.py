"""Row models of the triage file and the processing list (spec 004 data-model.md)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, field_validator

from pkpipe.domain.improvement import ImprovementItem
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
        return " ".join(v.split())[:TOPIC_CHARS] if isinstance(v, str) else v

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
            "resource": self.resource,
            "suggested": self.ai_suggested_category,
            "override": self.override or "",
            "state": self.state.value,
            "kind": "" if self.kind == "note" else self.kind,
            "needs_owner": "yes" if self.needs_owner else "",
            "topic": self.topic,
        }

    @classmethod
    def from_cells(cls, cells: dict[str, str]) -> TriageRow:
        mark = cells["confirm"].strip().lower()
        if mark not in ("[ ]", "[x]", ""):
            raise ValueError(f"bad checkbox {cells['confirm']!r}")
        return cls(
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
