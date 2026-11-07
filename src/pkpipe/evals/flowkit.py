"""In-process ports for flow evals (level 3): a real ``Vault`` on a temp directory behind the ``obsidian:*``
tools, a scripted learning backend, a scripted model, a virtual clock and a collecting notifier.

Flow cases (``datasets/flows/*.json``) describe the initial vault, the scripted responses and the owner's
decisions, and the expected end state; nothing here talks to a network or a model server.
"""

from __future__ import annotations

import json
import re
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pkpipe.domain.layout import DEFAULT_LAYOUT
from pkpipe.domain.triage import item_id_for
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault
from pkpipe.ports import Delivery, ModelError, ModelUnavailable, SchemaError

TRIAGE_FILE = DEFAULT_LAYOUT.triage_file
PROCESSING_LIST = DEFAULT_LAYOUT.processing_list
_ID_REF = re.compile(r"\{id:([^}]+)\}")


def expand_ids(text: str) -> str:
    """``{id:<resource>}`` -> the triage item id of that resource (cases name files, not hashes)."""
    return _ID_REF.sub(lambda m: item_id_for(m.group(1)), text)


class ScriptedLearning:
    """The real learning gateway (spec 012: URL filter, job store, timeout, failure reasons) over the fake
    backend. Per resource: ready after ``ready_after`` polls (default 1), ``fail`` -> the generation fails,
    ``never`` -> stays generating (the gateway timeout or the wait deadline ends it), ``down`` -> the
    service is unreachable, ``timeout_s`` -> gateway timeout (default: the pack's 90 minutes),
    ``deny`` -> the gateway's deny list, ``resolve`` -> synthetic DNS (host -> addresses; unknown hosts do
    not resolve)."""

    def __init__(
        self,
        ready_after: int = 1,
        fail: list[str] | None = None,
        never: list[str] | None = None,
        down: bool = False,
        timeout_s: float | None = None,
        deny: list[str] | None = None,
        resolve: dict[str, list[str]] | None = None,
        *,
        clock: Callable[[], float] | None = None,
        workdir: Path | None = None,
    ):
        from pkpipe.learning import LearningGateway
        from pkpipe.learning.backends import FakeBackend
        from pkpipe.learning.gateway import DEFAULT_TIMEOUT_S, GatewayConfig
        from pkpipe.learning.store import JobStore

        self.backend = FakeBackend(
            ready_after=ready_after,
            fail={item_id_for(r) for r in fail or []},
            never={item_id_for(r) for r in never or []},
            down=down,
        )
        config = GatewayConfig(
            timeout_s=timeout_s or DEFAULT_TIMEOUT_S,
            deny=[d.lower() for d in deny or []],
            audio_dir=(workdir or Path(tempfile.mkdtemp(prefix="pkpipe-eval-learning-"))) / "audio",
        )
        self.gateway = LearningGateway(
            self.backend,
            JobStore(":memory:"),
            config,
            resolver=lambda host: list((resolve or {}).get(host, [])),  # no real DNS lookups in an eval
            **({"clock": clock} if clock else {}),
        )

    def create(self, url: str, template: str, item_id: str = "") -> dict[str, Any]:
        return self.gateway.create(url, template, item_id)

    def status(self, job_id: str) -> dict[str, Any]:
        return self.gateway.status(job_id)

    def link(self, nb_id: str) -> dict[str, Any]:
        return self.gateway.link(nb_id)

    def audio(self, job_id: str) -> dict[str, Any]:
        return self.gateway.audio(job_id)


class ScriptedWeb:
    """``webfetch:fetch_page`` and ``websearch:search`` of flow cases.

    ``pages`` maps a URL to its HTML (the web-fetch MCP result shape, so the real secure-fetch steps run on
    it), a URL without a page is an HTTP 404 and ``down`` makes the server unreachable; ``search_results``
    scripts what the search returns (default: one fixed result). ``resolve`` is the ``resolver`` port: every
    host of a page resolves to a public address, any other host does not resolve (no
    real DNS in an eval)."""

    def __init__(
        self,
        pages: dict[str, str] | None = None,
        down: bool = False,
        search_results: list[dict[str, str]] | None = None,
    ):
        self.pages = dict(pages or {})
        self.down = down
        self.search_results = search_results
        self.queries: list[str] = []

    def resolve(self, host: str) -> list[str]:
        known = {url.split("/")[2].split(":")[0] for url in self.pages if "//" in url}
        return ["93.184.216.34"] if host in known else []

    def fetch_page(self, url: str, max_bytes: int | None = None) -> dict[str, Any]:
        if self.down:
            raise ConnectionError("web-fetch server is down")
        html = self.pages.get(url)
        if html is None:
            return {"status": "failed", "url": url, "http_status": 404, "reason": "http_404"}
        return {
            "status": "ok",
            "url": url,
            "final_url": url,
            "http_status": 200,
            "content_type": "text/html",
            "kind": "html",
            "html": html,
            "bytes": len(html.encode("utf-8")),
            "fetcher": "lightpanda",
        }

    def search(self, query: str, max_results: int = 5) -> dict[str, Any]:
        self.queries.append(query)
        if self.search_results is not None:  # a case that scripts the results (spec 025)
            return {"results": list(self.search_results)[:max_results]}
        return {
            "results": [{"title": "Example", "url": "https://example.org/result", "snippet": "synthetic"}]
        }


class VaultTools:
    """``ToolPort`` over a real :class:`Vault` (its path and owner-field rules apply), learning and web."""

    def __init__(
        self,
        vault_dir: Path,
        learning: ScriptedLearning | None = None,
        web: ScriptedWeb | None = None,
    ):
        vault = Vault(VaultPolicy(vault_dir=vault_dir))
        learning = learning or ScriptedLearning()
        web = web or ScriptedWeb()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._tools: dict[str, Callable[..., Any]] = {
            "obsidian:list_folder": vault.list_folder,
            "obsidian:read_note": vault.read_note,
            "obsidian:write_note": lambda path, body, front_matter=None, overwrite=False: vault.write_note(
                path, body, front_matter, overwrite
            ),
            "obsidian:move_note": vault.move_note,
            "webfetch:fetch_page": web.fetch_page,
            "websearch:search": web.search,
            "obsidian:read_triage": vault.read_triage,
            "obsidian:update_triage": vault.update_triage,
            "obsidian:upsert_processing_list": vault.upsert_processing_list,
            "obsidian:read_notes": vault.read_notes,
            "obsidian:read_improvements": vault.read_improvements,
            "obsidian:update_improvements": vault.update_improvements,
            "obsidian:read_tool_candidates": vault.read_tool_candidates,
            "obsidian:update_tool_candidates": vault.update_tool_candidates,
            "obsidian:quarantine_note": vault.quarantine_note,
            "learning:create": learning.create,
            "learning:status": learning.status,
            "learning:link": learning.link,
            "learning:audio": learning.audio,
        }

    def list_tools(self, allow: list[str]) -> list[str]:
        return sorted(t for t in self._tools if t in allow)

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        if tool not in self._tools:
            raise KeyError(f"unknown tool '{tool}'")
        self.calls.append((tool, arguments))
        return self._tools[tool](**arguments)


class ScriptedModel:
    """``ModelPort`` answering from rules; the first rule whose conditions all hold wins:

    * ``when``   – substring of the user prompt (for example a phrase of the note: input data, stable);
    * ``system`` – substring of the system prompt;
    * ``fields`` – the requested output schema requires (at least) these fields, e.g. ``["title", "note"]``,
      so a rule keeps matching when the session's instruction text is edited.

    A rule with ``"error": "SchemaError"`` (or ``"ModelUnavailable"``) raises that model error instead of
    replying (a model that kept answering invalid JSON after its retries).

    ``{canary}`` in a reply string becomes the canary of the system prompt (a scripted prompt leak).
    No match is a ``ModelError`` (a case must script every call)."""

    def __init__(self, rules: list[dict[str, Any]]):
        self.rules = rules
        self.calls = 0

    @staticmethod
    def _matches(rule: dict[str, Any], system: str, user: str, schema: dict[str, Any] | None) -> bool:
        if "when" in rule and rule["when"] not in user:
            return False
        if "system" in rule and rule["system"] not in system:
            return False
        if "fields" in rule and not set(rule["fields"]) <= set((schema or {}).get("required", [])):
            return False
        return True

    def complete(
        self, system: str, user: str, json_schema=None, profile: str = "default", images=None
    ) -> dict[str, Any]:
        self.calls += 1
        for rule in self.rules:
            if self._matches(rule, system, user, json_schema):
                if "error" in rule:
                    raise {"SchemaError": SchemaError, "ModelUnavailable": ModelUnavailable}.get(
                        rule["error"], ModelError
                    )("scripted model error")
                reply = json.dumps(rule["reply"])
                if "{canary}" in reply:
                    found = re.search(r"CNY-[0-9a-f]{16}", system)
                    reply = reply.replace("{canary}", found.group(0) if found else "no-canary-in-prompt")
                return json.loads(reply)
        raise ModelError("flow case has no scripted model reply for this prompt")


class ScriptedClassifier:
    """``ClassifierPort`` of flow cases: ``rules`` maps a lower-case substring to a score, ``labels`` to a
    Qwen3Guard-style ``[label, [categories]]``; anything else is clean (the deterministic detectors of the
    output guard, canary, markup and heuristics, still run for real)."""

    model = "scripted"

    def __init__(self, rules: dict[str, float] | None = None, labels: dict[str, list[Any]] | None = None):
        self.rules = {k.lower(): float(v) for k, v in (rules or {}).items()}
        self.labels = {k.lower(): v for k, v in (labels or {}).items()}

    def scan(self, text: str) -> dict[str, Any]:
        low = (text or "").lower()
        score = max([v for k, v in self.rules.items() if k in low], default=0.0)
        label, cats = "Safe", []
        for key, (lab, categories) in self.labels.items():
            if key in low:
                label, cats = lab, list(categories)
        return {
            "model": self.model,
            "max_score": score,
            "chunks": 1,
            "scores": [score],
            "label": label,
            "categories": cats,
        }


class VirtualClock:
    """``ClockPort`` whose time moves only when a step sleeps (waits finish instantly)."""

    def __init__(self, start: float = 1_800_000_000.0):
        self._now = start
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self._now += max(0.0, seconds)


class CollectingNotify:
    """NotifyPort that delivers nothing: it records ``(event, message)`` and reports one ok delivery per
    target channel (the explicit ``channels``, else a single ``eval`` channel)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, event: str, payload: dict[str, Any], channels: list[str] | None = None) -> list[Delivery]:
        self.sent.append((event, str(payload.get("message", ""))))
        return [Delivery(event, channel, True) for channel in (channels or ["eval"])]


def owner_decides(vault_dir: Path, confirm: list[str] = (), override: dict[str, str] | None = None) -> None:
    """Simulate the owner in Obsidian: tick confirm / write an override in the triage table (by resource)."""
    confirm_ids = {item_id_for(r) for r in confirm}
    override_ids = {item_id_for(r): c for r, c in (override or {}).items()}
    path = vault_dir / TRIAGE_FILE
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else None
        if cells and len(cells) >= 6 and re.fullmatch(r"[0-9a-f]{12}", cells[1]):
            if cells[1] in confirm_ids:
                cells[0] = "[x]"
            if cells[1] in override_ids:
                cells[4] = override_ids[cells[1]]
            line = "| " + " | ".join(cells) + " |"
        out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
