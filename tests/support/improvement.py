"""Helpers for the self-improvement flows (spec 015): synthetic journals, a scripted model that answers the
three sessions by output schema, and the owner's edits in the backlog table."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pkpipe.obsidian import improvements

BACKLOG = "2-Areas/Self-Improvement/Improvement-Backlog.md"
JOURNAL = "2-Areas/Journal"
ROUTES = {"weekly_review": ["discord_main"]}


def write_journal(vault: Path, day: str, body: str) -> None:
    path = vault / JOURNAL / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def journal_with(section: str, extra: str = "Synthetic diary line, not an improvement.") -> str:
    return (
        f"# Journal\n\n{extra}\n\n## Improvement\n\n{section}\n\n## Gratitude\n\nSynthetic gratitude line.\n"
    )


def backlog(vault: Path) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in improvements.read_improvements(vault / BACKLOG)["rows"]}


def owner_sets_status(vault: Path, item_id: str, status: str) -> None:
    """The owner edits the status cell in Obsidian."""
    path = vault / BACKLOG
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("|") and f"| {item_id} |" in line:
            cells = line.strip().strip("|").split("|")
            cells[0] = f" {status} "
            line = "|" + "|".join(cells) + "|"
        out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def schema_of(json_schema: dict[str, Any] | None) -> str:
    required = (json_schema or {}).get("required", [])
    return "items" if "items" in required else "evidence" if "evidence" in required else "verdict"


class ImprovementModel:
    """FakeModel callable: ``extract`` / ``evidence`` / ``verdict`` replies are given as functions of the
    user prompt; every call is recorded with its schema kind."""

    def __init__(self, extract=None, evidence=None, verdict=None):
        self.handlers = {"items": extract, "evidence": evidence, "verdict": verdict}
        self.kinds: list[str] = []

    def __call__(self, system: str, user: str) -> dict[str, Any]:
        kind = (
            "items"
            if 'the "Improvement" section' in user
            else "evidence"
            if "journal entries since it started" in user
            else "verdict"
        )
        self.kinds.append(kind)
        handler = self.handlers[kind]
        if handler is None:
            raise AssertionError(f"unexpected {kind} session")
        return handler(user)
