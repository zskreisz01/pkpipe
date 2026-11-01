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
PACK_ROUTES = {e: ["discord_main"] for e in ("triage_ready", "daily_summary", "security_flag")}
PROCESSING_LIST = "0-INBOX/Manual-Resource-Processing-List.md"


class FakeLearning:
    """``learning:*`` tools: the real gateway (URL filter, job store, timeout) over the fake backend.
    Each notebook reports 'generating' ``polls_until_ready`` times before it is ready."""

    def __init__(self, polls_until_ready: int = 2, fail: set[str] | None = None, workdir: Path | None = None):
        import tempfile

        from pkpipe.learning import LearningGateway
        from pkpipe.learning.backends import FakeBackend
        from pkpipe.learning.gateway import GatewayConfig
        from pkpipe.learning.store import JobStore

        self.backend = FakeBackend(ready_after=polls_until_ready + 1, fail=fail or set())
        audio = (workdir or Path(tempfile.mkdtemp(prefix="pkpipe-test-learning-"))) / "audio"
        self.gateway = LearningGateway(
            self.backend, JobStore(":memory:"), GatewayConfig(audio_dir=audio), resolver=lambda host: []
        )
        self.create, self.status = self.gateway.create, self.gateway.status
        self.link, self.audio = self.gateway.link, self.gateway.audio

    @property
    def created(self) -> list[str]:
        return [c["item_id"] for c in self.backend.created]

    @property
    def polls(self) -> dict[str, int]:
        return {k.replace("job-", "nb-", 1): v for k, v in self.backend.polls.items()}


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
            "obsidian:read_notes": lambda paths: vault.read_notes(paths),
            "obsidian:read_improvements": lambda: vault.read_improvements(),
            "obsidian:update_improvements": lambda rows: vault.update_improvements(rows),
            "learning:create": learning.create,
            "learning:status": learning.status,
            "learning:link": learning.link,
            "learning:audio": learning.audio,
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
