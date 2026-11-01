"""Use cases behind the config UI and the app MCP server (spec 014).

Both inbound adapters (FastAPI routes, FastMCP tools) call only these methods, so the rules (eval gate before
publish, owner-only decisions, one active run per flow, audit of every owner action) live in one place.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pkpipe.adapters.app_store_sqlite import AppStore
from pkpipe.api.publish import Publisher, PublishError
from pkpipe.api.workspace import ConfigWorkspace, WorkspaceError, category_entry
from pkpipe.domain.triage import ItemState
from pkpipe.engine.runs import ACTIVE, RunService
from pkpipe.engine.snapshot import ConfigSnapshot
from pkpipe.evals.gate import GateDecision, PublishGate
from pkpipe.obsidian.errors import VaultError
from pkpipe.obsidian.vault import Vault

log = logging.getLogger("pkpipe.api")
MAX_RESOURCE_CHARS = 300


class NotFound(LookupError):
    pass


class Refused(ValueError):
    """The request is valid but the rules refuse it (state, conflict, gate)."""


class RunLauncher:
    """Starts flow runs in the background (a run can take minutes) and refuses a second active run of the
    same flow, like the scheduler's ``max_instances=1``."""

    def __init__(self, runs: RunService, *, workers: int = 4, wait_s: float = 10.0):
        self.runs, self.wait_s = runs, wait_s
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pkpipe-run")
        self._lock = threading.Lock()
        self._starting: set[str] = set()
        self.futures: dict[str, Any] = {}

    def launch(self, flow_id: str, *, trigger: str, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        if inputs is not None and not isinstance(inputs, dict):
            raise ValueError("inputs must be a JSON object")
        try:
            self.runs.config().flow(flow_id)
        except KeyError as exc:
            raise NotFound(str(exc.args[0])) from None
        with self._lock:
            active = [r for r in self.runs.list(flow_id=flow_id) if r.status in ACTIVE]
            if flow_id in self._starting or active:
                raise Refused(f"flow '{flow_id}' already has an active run")
            self._starting.add(flow_id)
        run_id = str(uuid.uuid4())

        def work() -> None:
            try:
                self.runs.start(flow_id, trigger=trigger, inputs=inputs or {}, run_id=run_id)
            finally:
                with self._lock:
                    self._starting.discard(flow_id)

        future = self._pool.submit(work)
        self.futures[run_id] = future
        deadline = time.monotonic() + self.wait_s
        while time.monotonic() < deadline:
            run = self.runs.get(run_id)
            if run is not None:
                return run.record()
            if future.done():
                future.result()  # raises the start error (for example an invalid config)
            time.sleep(0.02)
        raise Refused("the run did not start in time")

    def wait(self, run_id: str, timeout: float = 60.0) -> None:
        """Block until a launched run finished (tests, shutdown)."""
        fut = self.futures.get(run_id)
        if fut is not None:
            fut.result(timeout)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=not wait)


@dataclass
class AppServices:
    runs: RunService
    store: AppStore
    workspace: ConfigWorkspace
    publisher: Publisher
    launcher: RunLauncher
    vault: Vault | None
    gate_factory: Callable[[ConfigSnapshot], PublishGate]
    on_config_change: Callable[[], None] | None = None

    # -- runs -----------------------------------------------------------------------------------------------
    def flows(self) -> list[str]:
        return sorted(self.runs.config().flows())

    def start_run(self, flow_id: str, *, trigger: str, actor: str, inputs: dict | None = None) -> dict:
        run = self.launcher.launch(flow_id, trigger=trigger, inputs=inputs)
        self.store.audit(actor, "run_flow", flow_id, run_id=run["run_id"])
        return run

    def list_runs(
        self, *, status: str | None = None, flow_id: str | None = None, limit: int = 50
    ) -> list[dict]:
        rows = [r.record() for r in self.runs.list(status=status, flow_id=flow_id)]
        return list(reversed(rows))[:limit]

    def get_run(self, run_id: str) -> dict[str, Any]:
        """Run record plus a safe summary of its progress: the *names* of finished outputs, never their
        values (they can hold raw external content, which must not reach a chat agent)."""
        run = self.runs.get(run_id)
        if run is None:
            raise NotFound(f"unknown run '{run_id}'")
        record = run.record()
        record["steps_done"] = self._steps_done(run_id)
        return record

    def _steps_done(self, run_id: str) -> list[str]:
        try:
            tup = self.runs.checkpointer.get_tuple({"configurable": {"thread_id": run_id}})
        except Exception:  # noqa: BLE001 - a missing checkpoint just means no progress info
            return []
        if tup is None:
            return []
        values = tup.checkpoint.get("channel_values", {})
        return sorted({*(values.get("outputs") or {}), *(values.get("item_outputs") or {})})

    # -- triage ---------------------------------------------------------------------------------------------
    def _vault(self) -> Vault:
        if self.vault is None:
            raise Refused("no vault configured (PKPIPE_VAULT_DIR or --vault)")
        return self.vault

    def categories(self) -> set[str]:
        return {c["id"] for c in self.runs.config().data.get("categories", []) if c.get("enabled", True)}

    def list_triage(self, state: str | None = None) -> dict[str, Any]:
        data = self._vault().read_triage()
        rows = []
        for r in data["rows"]:
            if state and r["state"] != state:
                continue
            resource = r["resource"]
            rows.append(
                {
                    **r,
                    "resource": resource[:MAX_RESOURCE_CHARS],
                    "category": r["override"] or r["ai_suggested_category"],
                }
            )
        return {"rows": rows, "invalid": len(data["invalid"]), "conflicts": data["conflicts"]}

    def _decide(self, actor: str, action: str, item_id: str, **decision: Any) -> dict[str, Any]:
        try:
            row = self._vault().owner_decide(item_id, categories=self.categories(), **decision)
        except VaultError as exc:
            self.store.audit(actor, f"{action}_refused", item_id, reason=str(exc))
            raise Refused(str(exc)) from None
        self.store.audit(actor, action, item_id, **{k: v for k, v in decision.items() if v is not None})
        return row

    def confirm_item(self, item_id: str, *, actor: str, confirm: bool = True) -> dict[str, Any]:
        return self._decide(actor, "confirm_item" if confirm else "unconfirm_item", item_id, confirm=confirm)

    def override_item(self, item_id: str, category: str, *, actor: str) -> dict[str, Any]:
        if not category or not category.strip():
            raise Refused("an override needs a category")
        return self._decide(actor, "override_item", item_id, override=category)

    def release_quarantine(self, item_id: str, *, actor: str) -> dict[str, Any]:
        try:
            row = self._vault().owner_release_quarantine(item_id)
        except VaultError as exc:
            self.store.audit(actor, "release_quarantine_refused", item_id, reason=str(exc))
            raise Refused(str(exc)) from None
        self.store.audit(actor, "release_quarantine", item_id, state=ItemState.PENDING.value)
        return row

    # -- config drafts --------------------------------------------------------------------------------------
    def open_draft(self) -> dict[str, Any]:
        """The newest open draft, or a new one."""
        open_drafts = self.store.list_drafts("open")
        return open_drafts[-1] if open_drafts else self.store.create_draft()

    def _draft(self, draft_id: str, *, must_be_open: bool = True) -> dict[str, Any]:
        draft = self.store.get_draft(draft_id)
        if draft is None:
            raise NotFound(f"unknown draft '{draft_id}'")
        if must_be_open and draft["status"] != "open":
            raise Refused(f"draft '{draft_id}' is {draft['status']}")
        return draft

    def edit_file(
        self,
        draft_id: str,
        path: str,
        content: str | None,
        *,
        base_hash: str | None,
        rev: int | None,
        actor: str = "ui",
    ) -> dict[str, Any]:
        draft = self._draft(draft_id)
        files = self.workspace.with_file(draft["files"], path, content, base_hash)
        saved = self.store.save_draft(draft_id, files, expected_rev=rev)
        self.store.audit(actor, "edit_draft_file", draft_id, path=path, deleted=content is None)
        return saved

    def discard_file(self, draft_id: str, path: str, *, rev: int | None) -> dict[str, Any]:
        draft = self._draft(draft_id)
        files = {k: v for k, v in draft["files"].items() if k != path}
        return self.store.save_draft(draft_id, files, expected_rev=rev)

    def discard_draft(self, draft_id: str) -> dict[str, Any]:
        draft = self._draft(draft_id)
        self.store.audit("ui", "discard_draft", draft_id)
        return self.store.save_draft(draft_id, draft["files"], expected_rev=None, status="discarded")

    def add_category(
        self,
        draft_id: str,
        *,
        category_id: str,
        label: str,
        subflow: str,
        para_path: str,
        triage_hint: str,
        rev: int | None,
    ) -> dict[str, Any]:
        """Add a category entry to the draft's ``categories.json`` (overlay layer, merged by id)."""
        import json

        if not category_id.replace("-", "").replace("_", "").isalnum() or not category_id.islower():
            raise Refused("category id: lower case letters, digits, '-' and '_' only")
        draft = self._draft(draft_id)
        current = draft["files"].get("categories.json", {}).get("content")
        if current is None:
            current = self.workspace.overlay_text("categories.json")
        entries = json.loads(current) if current else []
        if not isinstance(entries, list):
            raise Refused("categories.json of the overlay is not a list")
        if any(e.get("id") == category_id for e in entries) or category_id in self.categories():
            raise Refused(f"category '{category_id}' already exists")
        entries.append(category_entry(category_id, label, subflow, para_path, triage_hint))
        text = json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
        return self.edit_file(draft_id, "categories.json", text, base_hash=None, rev=rev)

    def validate_draft(self, draft_id: str) -> dict[str, Any]:
        draft = self._draft(draft_id)
        return self.workspace.validate(draft["files"]).to_dict()

    def diff_draft(self, draft_id: str) -> list[dict[str, str]]:
        return self.workspace.diff(self._draft(draft_id, must_be_open=False)["files"])

    def _gate(self, draft: dict[str, Any], override_reason: str | None) -> tuple[GateDecision | None, dict]:
        result = self.workspace.validate(draft["files"])
        if not result.ok:
            return None, {"stage": "validate", **result.to_dict()}
        snapshot = self.workspace.draft_snapshot(draft["files"])
        gate = self.gate_factory(snapshot)
        decision = gate.check(self.workspace.current(), snapshot, override_reason=override_reason)
        return decision, {"stage": "gate", **decision.to_dict(), "warnings": result.warnings}

    def smoke_eval(self, draft_id: str) -> dict[str, Any]:
        """Dry run of the publish gate: validation, then the affected evals. Nothing is written."""
        draft = self._draft(draft_id)
        _, report = self._gate(draft, None)
        return report

    def publish_draft(
        self, draft_id: str, *, summary: str, override_reason: str | None = None, actor: str = "ui"
    ) -> dict[str, Any]:
        """validate -> eval gate -> git commit. A blocked or invalid draft is never written."""
        draft = self._draft(draft_id)
        if not draft["files"]:
            raise Refused("the draft has no changes")
        summary = (
            " ".join((summary or "").split())[:120] or f"update {', '.join(sorted(draft['files']))[:80]}"
        )
        decision, report = self._gate(draft, override_reason)
        if decision is None or not decision.allowed:
            self.store.audit(actor, "publish_blocked", draft_id, stage=report["stage"])
            return {"published": False, **report}
        try:
            record = self.publisher.publish(draft_id, draft["files"], summary=summary, decision=report)
        except (PublishError, WorkspaceError) as exc:
            self.store.audit(actor, "publish_failed", draft_id, reason=str(exc))
            raise Refused(str(exc)) from None
        self.store.save_draft(draft_id, draft["files"], expected_rev=None, status="published")
        self._config_changed()
        self.store.audit(
            actor,
            "publish",
            draft_id,
            commit=record["commit_sha"],
            gate=report.get("status"),
            override=bool(override_reason) and report.get("status") == "overridden",
        )
        return {"published": True, "publish": record, **report}

    def rollback(self, publish_id: str, *, actor: str = "ui") -> dict[str, Any]:
        try:
            record = self.publisher.rollback(publish_id)
        except KeyError as exc:
            raise NotFound(str(exc.args[0])) from None
        except (PublishError, WorkspaceError) as exc:
            raise Refused(str(exc)) from None
        self.store.audit(actor, "rollback", publish_id, commit=record["commit_sha"])
        self._config_changed()
        return record

    def _config_changed(self) -> None:
        """Hook after a publish or rollback (``pkpipe serve`` re-syncs schedules). Never fails the call."""
        if self.on_config_change is not None:
            try:
                self.on_config_change()
            except Exception:  # noqa: BLE001 - the config is already committed; a sync error is logged only
                log.exception("config change hook failed")

    def publish_diff(self, publish_id: str) -> str:
        try:
            return self.publisher.diff(publish_id)
        except KeyError as exc:
            raise NotFound(str(exc.args[0])) from None


def default_vault(vault_dir: Path | None) -> Vault | None:
    if vault_dir is None:
        return None
    from pkpipe.obsidian.policy import VaultPolicy

    return Vault(VaultPolicy(vault_dir=vault_dir))
