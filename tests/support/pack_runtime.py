"""Run the public pack's flows on fake ports: FakeTools backed by a real ``Vault`` on a temp vault, a fake
learning backend, FakeModel, FakeNotify, FakeClock (spec 005 SC-001)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault
from tests.fakes import FakeTools

TRIAGE_FILE = "0-INBOX/Inbox-Triage.md"
PROCESSING_LIST = "0-INBOX/Manual-Resource-Processing-List.md"


class FakeLearning:
    """learning:create/status/link: each notebook reports 'running' ``polls_until_ready`` times."""

    def __init__(self, polls_until_ready: int = 2, fail: set[str] | None = None):
        self.polls_until_ready = polls_until_ready
        self.fail = fail or set()
        self.polls: dict[str, int] = {}
        self.created: list[str] = []

    def create(self, resource: str, item_id: str) -> dict[str, Any]:
        self.created.append(item_id)
        return {"nb_id": f"nb-{item_id}"}

    def status(self, nb_id: str) -> dict[str, Any]:
        self.polls[nb_id] = self.polls.get(nb_id, 0) + 1
        if nb_id.removeprefix("nb-") in self.fail:
            return {"status": "failed"}
        return {"status": "ready" if self.polls[nb_id] > self.polls_until_ready else "running"}

    def link(self, nb_id: str) -> dict[str, Any]:
        return {"url": f"https://example.invalid/notebook/{nb_id}"}


def vault_tools(vault_dir: Path, learning: FakeLearning | None = None) -> FakeTools:
    vault = Vault(VaultPolicy(vault_dir=vault_dir))
    learning = learning or FakeLearning()
    return FakeTools(
        {
            "obsidian:list_folder": lambda path: vault.list_folder(path),
            "obsidian:read_note": lambda path: vault.read_note(path),
            "obsidian:write_note": lambda path, body, front_matter=None, overwrite=False: vault.write_note(
                path, body, front_matter, overwrite
            ),
            "obsidian:read_triage": lambda: vault.read_triage(),
            "obsidian:update_triage": lambda rows: vault.update_triage(rows),
            "obsidian:upsert_processing_list": lambda rows: vault.upsert_processing_list(rows),
            "learning:create": learning.create,
            "learning:status": learning.status,
            "learning:link": learning.link,
        }
    )


def owner_decides(vault_dir: Path, confirm: list[str] = (), override: dict[str, str] | None = None) -> None:
    """Simulate the owner ticking confirm / writing an override in Obsidian (edits the Markdown table)."""
    path = vault_dir / TRIAGE_FILE
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else None
        if cells and len(cells) == 6 and re.fullmatch(r"[0-9a-f]{12}", cells[1]):
            if cells[1] in confirm:
                cells[0] = "[x]"
            if override and cells[1] in override:
                cells[4] = override[cells[1]]
            line = "| " + " | ".join(cells) + " |"
        out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def fake_model_reply(categories_by_word: dict[str, str], notes: dict[str, str] | None = None):
    """FakeModel callable: triage sessions get a category by keyword, summarize sessions a note."""
    notes = notes or {}

    def reply(system: str, user: str) -> dict[str, Any]:
        if system.startswith("You sort captured resources"):
            for word, cat in categories_by_word.items():
                if word in user:
                    return {"category": cat, "confidence": 0.9, "reason": f"mentions {word}"}
            return {"category": "misc", "confidence": 0.3, "reason": "unclear"}
        for word, note in notes.items():
            if word in user:
                return {"title": f"About {word}", "note": note}
        return {"title": "Resource", "note": "A short synthetic note."}

    return reply
