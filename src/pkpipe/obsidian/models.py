"""Row models of the triage file and the processing list (spec 004 data-model.md)."""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, field_validator

from pkpipe.domain.triage import ItemState


class ProcessingStatus(StrEnum):
    UNPROCESSED = "unprocessed"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    DONE = "done"
    MANUAL = "manual"


def item_id_for(resource: str) -> str:
    """Stable triage item id: hash of the resource."""
    return hashlib.sha256(resource.strip().encode("utf-8")).hexdigest()[:12]


class TriageRow(BaseModel):
    item_id: str
    resource: str
    ai_suggested_category: str
    confirm: bool = False
    override: str | None = None
    state: ItemState = ItemState.SUGGESTED

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
