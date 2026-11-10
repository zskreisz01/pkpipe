"""Run the public pack's flows on fake ports: FakeTools backed by a real ``Vault`` on a temp vault, a fake
learning backend, FakeModel, FakeNotify, FakeClock (spec 005 SC-001)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault
from tests.fakes import FakeSearch, FakeTools, FakeWeb

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


TOOL_PAGE = "<html><head><title>Example tool</title></head><body><p>A fictional CLI tool.</p></body></html>"


def synthetic_web() -> FakeWeb:
    """The page of ``synthetic_vault``'s tool note (``source: https://example.invalid/tool``)."""
    return FakeWeb({"https://example.invalid/tool": TOOL_PAGE})


def vault_tools(
    vault_dir: Path,
    learning: FakeLearning | None = None,
    web: FakeWeb | None = None,
    search: FakeSearch | None = None,
    policy: VaultPolicy | None = None,
) -> FakeTools:
    vault = Vault(policy or VaultPolicy(vault_dir=vault_dir))
    learning = learning or FakeLearning()
    web, search = web or FakeWeb(), search or FakeSearch()
    return FakeTools(
        {
            "obsidian:move_note": lambda src, dst: vault.move_note(src, dst),
            "webfetch:fetch_page": web.fetch_page,
            "websearch:search": search.search,
            "obsidian:list_folder": lambda path: vault.list_folder(path),
            "obsidian:read_note": lambda path: vault.read_note(path),
            "obsidian:write_note": lambda path, body, front_matter=None, overwrite=False: vault.write_note(
                path, body, front_matter, overwrite
            ),
            "obsidian:read_triage": lambda: vault.read_triage(),
            "obsidian:update_triage": lambda rows: vault.update_triage(rows),
            "obsidian:upsert_processing_list": lambda rows: vault.upsert_processing_list(rows),
            "obsidian:read_notes": lambda paths: vault.read_notes(paths),
            "obsidian:unreviewed_notes": lambda folder, category: vault.unreviewed_notes(folder, category),
            "obsidian:failed_items": lambda: vault.failed_items(),
            "obsidian:write_journal_todos": lambda day, lines, heading="## pkpipe todo": (
                vault.write_journal_todos(day, lines, heading)
            ),
            "obsidian:read_improvements": lambda: vault.read_improvements(),
            "obsidian:update_improvements": lambda rows: vault.update_improvements(rows),
            "obsidian:read_tool_candidates": lambda: vault.read_tool_candidates(),
            "obsidian:update_tool_candidates": lambda rows: vault.update_tool_candidates(rows),
            "obsidian:quarantine_note": vault.quarantine_note,
            "obsidian:inbox_remove_entries": lambda entries: vault.inbox_remove_entries(entries),
            "obsidian:read_processing_list": lambda: vault.read_processing_list(),
            "obsidian:read_in_progress": lambda: vault.read_in_progress(),
            "obsidian:inbox_in_progress": lambda rows, archive_ids=None, restore_ids=None: (
                vault.inbox_in_progress(rows, archive_ids, restore_ids)
            ),
            "obsidian:read_image": lambda name: vault.read_image(name),
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
        if cells and len(cells) >= 6 and re.fullmatch(r"[0-9a-f]{12}", cells[1]):
            if cells[1] in confirm:
                cells[0] = "[x]"
            if override and cells[1] in override:
                cells[4] = override[cells[1]]
            line = "| " + " | ".join(cells) + " |"
        out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


_DATA = re.compile(
    r"### (?P<ref>[\w.]+)\n<<<DATA (?P<tok>[0-9a-f]+)>>>\n(?P<text>.*?)\n<<<END DATA (?P=tok)>>>", re.S
)


def prompt_inputs(user: str) -> dict[str, str]:
    """The datamarked inputs of a session prompt by reference (``sanitize.title``, ``item.extract``, ...)."""
    return {m["ref"]: m["text"] for m in _DATA.finditer(user)}


def fake_model_reply(
    categories_by_word: dict[str, str],
    notes: dict[str, str] | None = None,
    relevance: dict[str, dict[str, Any]] | None = None,
):
    """FakeModel callable for the whole public pack: triage by keyword, the secure-fetch Reader (title of the
    page), the tool loop (one web search, then the final answer) and every category session by its output
    schema; ``notes`` maps a word of the prompt to the text of a ``note`` field (a scripted injection),
    ``relevance`` a word of the prompt to the answer of the system relevance session (default: none)."""
    notes = notes or {}
    relevance = relevance or {}

    def reply(system: str, user: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        if system.startswith("You sort captured resources"):
            for word, cat in categories_by_word.items():
                if word in user:
                    return {"category": cat, "confidence": 0.9, "reason": f"mentions {word}"}
            return {"category": "misc", "confidence": 0.3, "reason": "unclear"}
        props = set((schema or {}).get("properties", {}))
        data = prompt_inputs(user)
        if props == {"situation", "cause", "next_step"}:  # spec 031: the situation of an unfinished item
            facts = data.get("item.facts_text") or ""
            return {
                "situation": "Scripted situation: " + " ".join(facts.split())[:120],
                "cause": "scripted cause",
                "next_step": "scripted next step",
            }
        if props == {"action", "tool", "arguments", "answer"}:  # the tool loop (llm_tools)
            if "## Tool results" in user:
                return {"action": "final", "answer": "done"}
            return {"action": "call", "tool": "websearch:search", "arguments": {"query": "example tool"}}
        title = (data.get("sanitize.title") or "").strip() or "Resource"
        if "injection_suspected" in props:  # the secure-fetch Reader
            return {
                "title": title,
                "summary": f"A synthetic page about {title}.",
                "facts": [f"{title} is a synthetic fact"],
                "links": [],
                "injection_suspected": False,
                "evidence": None,
            }
        if "relevance" in props:  # tool.system_relevance (spec 016)
            for word, answer in relevance.items():
                if word in user:
                    return answer
            return {"relevance": "none", "component": "", "benefit": "", "effort": "S"}
        name = (json.loads(data["item.extract"]).get("title") if "item.extract" in data else None) or title
        if "extract" in data:  # marketing.synthesize: the idea of the extract session
            name = json.loads(data["extract"]).get("idea", name).split(":")[0]
        if "maintainer" in props:  # tool.research
            return {
                "name": name,
                "summary": f"{name} does something useful.",
                "maintainer": "unknown",
                "license": "MIT",
                "pricing": "free",
                "maturity": "young",
                "ecosystem": "python",
                "sources": ["https://example.org/result"],
            }
        if "alternatives" in props and "recommendation" not in props:  # tool.compare
            return {
                "alternatives": [
                    {
                        "name": "altool",
                        "license": "MIT",
                        "cost": "free",
                        "runs_locally": "yes",
                        "api": "yes",
                        "maturity": "mature",
                    }
                ]
            }
        if "adoption_cost" in props:  # tool.fit_*
            return {"fit": "fits the example project", "replaces": "nothing", "adoption_cost": "low"}
        if "recommendation" in props:  # tool.synthesize
            out = {
                "title": f"{name} evaluation",
                "summary": f"{name} in short.",
                "alternatives": ["altool"],
                "recommendation": "trial",
                "justification": "Looks useful.",
            }
            if "fit_personal" in data.get("*", "") or "### fit_personal" in user:
                out["fit_personal"] = "fits the example project"
            if "### fit_enterprise" in user:
                out["fit_enterprise"] = "fits the example programme"
            return out
        if "hook" in props:  # marketing.extract
            return {"idea": f"{name}: an idea", "hook": "a catchy hook", "why_it_works": ["simple"]}
        out = {"title": f"About {name}", "note": "A short synthetic note."}
        if "suggestion" in props:  # misc.evaluate
            out["suggestion"] = None
        for word, note in notes.items():
            if word in user:
                out["note"] = note
        return out

    reply.wants_schema = True  # type: ignore[attr-defined]
    return reply
