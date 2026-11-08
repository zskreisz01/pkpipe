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
from pkpipe.domain import session_plan as plans
from pkpipe.domain.triage import DEFAULT_MAX_RETRIES, ItemState, is_unreviewed, resolve_category
from pkpipe.domain.vault_snapshot import SnapshotError, UnknownSnapshot
from pkpipe.engine.restore import restore_run
from pkpipe.engine.runs import ACTIVE, RunService
from pkpipe.engine.snapshot import ConfigSnapshot
from pkpipe.evals.gate import GateDecision, PublishGate
from pkpipe.obsidian.errors import VaultError
from pkpipe.obsidian.vault import Vault
from pkpipe.ports import ImageError, ImagePreparePort, SnapshotPort

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
        self._claim(flow_id)
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

    def _claim(self, flow_id: str) -> None:
        """Reserve ``flow_id`` for one new run, or raise Refused while another run of it is active."""
        with self._lock:
            active = [r for r in self.runs.list(flow_id=flow_id) if r.status in ACTIVE]
            if flow_id in self._starting or active:
                raise Refused(f"flow '{flow_id}' already has an active run")
            self._starting.add(flow_id)

    def run_scheduled(self, flow_id: str) -> bool:
        """Scheduler entry (blocking, on the scheduler's thread): the same one-active-run rule as the UI and
        MCP, so a cron tick never starts a second run next to a UI- or chat-started one. False = skipped."""
        try:
            self._claim(flow_id)
        except Refused:
            log.warning("scheduled run of '%s' skipped: the flow already has an active run", flow_id)
            return False
        try:
            self.runs.start(flow_id, trigger="schedule")
        finally:
            with self._lock:
                self._starting.discard(flow_id)
        return True

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
    #: Where released quarantine items become eval candidates (spec 009); None = not exported.
    candidates_dir: Path | None = None
    #: Vault snapshots of writing runs (spec 019); None = no vault configured, nothing to restore.
    snapshots: SnapshotPort | None = None
    #: Decodes an INBOX image for the UI thumbnail (spec 023): the metadata-free scaled JPEG; None = the
    #: file as is.
    image_preparer: ImagePreparePort | None = None

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

    # -- snapshots and restore (spec 019) -------------------------------------------------------------------
    def _snapshots(self) -> SnapshotPort:
        if self.snapshots is None:
            raise Refused("no vault configured (PKPIPE_VAULT_DIR or --vault): there are no snapshots")
        return self.snapshots

    def list_snapshots(self) -> list[dict[str, Any]]:
        return [i.model_dump(mode="json") for i in self._snapshots().list()]

    def snapshot_of_run(self, run_id: str) -> dict[str, Any] | None:
        """The snapshot row of a run (for the run page), or None when it took none or no vault is set."""
        if self.snapshots is None:
            return None
        return next((i.model_dump(mode="json") for i in self.snapshots.list() if i.run_id == run_id), None)

    def restore_run(
        self, run_id: str, *, apply: bool = False, force: bool = False, actor: str
    ) -> dict[str, Any]:
        """Owner action (spec 019 FR-004): the restore plan of a run, applied only with ``apply``. Audited
        when applied (counts and the safety snapshot, never paths); a refusal is audited too."""
        port = self._snapshots()
        try:
            plan = restore_run(port, run_id, apply=apply, force=force, runs=self.runs)
        except UnknownSnapshot as exc:
            raise NotFound(str(exc)) from None
        except SnapshotError as exc:
            if apply:
                self.store.audit(actor, "restore_run_refused", run_id, reason=str(exc))
            raise Refused(str(exc)) from None
        counts = plan.counts()
        if apply:
            self.store.audit(
                actor, "restore_run", plan.run_id, force=force, **counts, safety=plan.safety_snapshot
            )
        return {**plan.to_json(), "counts": counts}

    # -- triage ---------------------------------------------------------------------------------------------
    def _vault(self) -> Vault:
        if self.vault is None:
            raise Refused("no vault configured (PKPIPE_VAULT_DIR or --vault)")
        return self.vault

    def categories(self) -> set[str]:
        return {c["id"] for c in self.runs.config().data.get("categories", []) if c.get("enabled", True)}

    def category_map(self) -> dict[str, dict[str, str]]:
        """Enabled categories as ``{id: {label}}`` (what an owner decision is resolved against)."""
        return {
            c["id"]: {"label": c.get("label", c["id"])}
            for c in self.runs.config().data.get("categories", [])
            if c.get("enabled", True)
        }

    def list_triage(
        self,
        state: str | None = None,
        *,
        category: str | None = None,
        needs_owner: bool | None = None,
        with_comment: bool = True,
    ) -> dict[str, Any]:
        """The triage rows for the owner channels, optionally filtered by state, effective category and
        ``needs_owner``. ``with_comment=False`` is for a caller that is not the owner (the agent token of the
        app MCP): the owner's comment is for the owner and the sessions' declared input only."""
        data = self._vault().read_triage()
        layout = self._vault().policy.layout
        failed = self._failed_ids()
        retries = self.store.retry_counts() if failed else {}
        held = self._held_ids() if failed else set()
        limit = self.retry_max()
        rows = []
        for r in data["rows"]:
            effective = r["override"] or r["ai_suggested_category"]
            if state and r["state"] != state:
                continue
            if category and effective != category:
                continue
            if needs_owner is not None and bool(r["needs_owner"]) != needs_owner:
                continue
            row = {**r, "resource": r["resource"][:MAX_RESOURCE_CHARS], "category": effective}
            if not with_comment:
                row.pop("comment", None)
            if r["state"] == ItemState.PROCESSED.value and r["item_id"] in failed:
                # spec 027: the processing of this item failed; the owner may send it through again
                done = retries.get(r["item_id"], 0)
                row.update(failed=True, retries=done, retryable=done < limit)
                if r["item_id"] in held:  # spec 029: only the injection detector fired; the owner decides
                    row.update(review=self.review_summary(r["item_id"]), review_open=True)
            if r["state"] == ItemState.QUARANTINED.value:
                row["quarantine"] = self.quarantine_summary(r["item_id"])
            if r["state"] == ItemState.AWAITING_CATEGORY.value and r["override"]:
                kind, slug = resolve_category(r["override"], self.category_map())
                if kind == "new" and slug:
                    row["proposal_note"] = plans.proposal_path(layout.proposals_dir, slug, r["item_id"])
            rows.append(row)
        return {"rows": rows, "invalid": len(data["invalid"]), "conflicts": data["conflicts"]}

    # -- retry of failed items (spec 027) -------------------------------------------------------------------
    def retry_max(self) -> int:
        """The owner's retries per failed item (``triage.retry_max``, default 3)."""
        value = (self.runs.config().data.get("triage") or {}).get("retry_max", DEFAULT_MAX_RETRIES)
        return value if isinstance(value, int) and value >= 1 else DEFAULT_MAX_RETRIES

    def _failed_ids(self) -> dict[str, str]:
        """``{item id: category}`` of the failed processing-list lines; empty when the list cannot be read."""
        try:
            return {i["item_id"]: i["category"] for i in self._vault().failed_items()["items"]}
        except (VaultError, Refused):
            return {}

    def failed_summary(self) -> dict[str, Any]:
        """Counts for the triage page and the report: failed items, how many may still be retried."""
        failed = self._failed_ids()
        done = self.store.retry_counts() if failed else {}
        limit = self.retry_max()
        rows = {r["item_id"]: r for r in self._vault().read_triage()["rows"]}
        held = self._held_ids()
        retryable = sum(
            1
            for i in failed
            if rows.get(i, {}).get("state") == ItemState.PROCESSED.value
            and done.get(i, 0) < limit
            and i not in held
        )
        return {
            "failed": len(failed),
            "retryable": retryable,
            "held": len([i for i in failed if i in held]),
            "capped": len(failed) - retryable - len([i for i in failed if i in held]),
            "max": limit,
        }

    # -- classifier warnings held for the owner (spec 029) ------------------------------------------
    def _held_ids(self) -> set[str]:
        """Items held after a classifier warning that the owner has not accepted yet."""
        try:
            return {i for i, r in self.store.held().items() if not r["waived"]}
        except Exception:  # an old app DB without the table is created on first use; never block a list
            return set()

    def review_summary(self, item_id: str) -> dict[str, Any] | None:
        """The held item's address, evidence and the fixed sentences that explain it (no page text)."""
        from pkpipe.security.evidence import interpret

        rec = self.store.review_of(item_id)
        if rec is None:
            return None
        return {
            "url": rec["url"],
            "waived": rec["waived"],
            "evidence": rec["evidence"],
            "lines": interpret(rec["evidence"]) if rec["evidence"] else [],
        }

    def accept_warning(self, item_id: str, *, actor: str) -> dict[str, Any]:
        """Owner decision (spec 029): accept the classifier warning of a held item. The item goes through the
        next release run again (like a retry, bounded by ``triage.retry_max``) and its secure fetch lets the
        page pass when the injection detector is the only finding; every other detector still applies."""
        if self.store.review_of(item_id) is None:
            self.store.audit(actor, "accept_warning_refused", item_id, reason="no warning held")
            raise Refused("no classifier warning is held for this item")
        result = self.retry_item(item_id, actor=actor)
        self.store.waive(item_id)
        self.store.audit(actor, "accept_classifier_warning", item_id)
        return {**result, "accepted": True}

    def quarantine_summary(self, item_id: str) -> dict[str, Any]:
        """Where the quarantined item came from and why (spec 029): address, flags, scores, the fixed
        sentences that explain the classifier evidence. From the note's front matter, never its body."""
        from pkpipe.security.evidence import interpret

        try:
            info = self._vault().quarantine_info(item_id)
        except (VaultError, Refused):
            return {}
        if info and info.get("classifier"):
            info["lines"] = interpret(info["classifier"])
        return info

    def retry_item(self, item_id: str, *, actor: str) -> dict[str, Any]:
        """Owner decision (spec 027): a processed item whose processing failed goes back to ``released`` for
        the next release run. Bounded by ``triage.retry_max`` per item; audited."""
        done = self.store.retry_count(item_id)
        try:
            row = self._vault().owner_retry(item_id, retries=done, max_retries=self.retry_max())
        except VaultError as exc:
            self.store.audit(actor, "retry_item_refused", item_id, reason=str(exc))
            raise Refused(str(exc)) from None
        count = self.store.bump_retry(item_id)
        self.store.audit(actor, "retry_item", item_id, retries=count, state=ItemState.RELEASED.value)
        return {**row, "retries": count}

    def retry_failed(self, *, actor: str, item_ids: list[str] | None = None) -> dict[str, Any]:
        """Retry every failed item that may still be retried (or the named ones): the bulk action of the UI
        and the app MCP. Items that cannot be retried (quarantined, over the limit, not failed) are skipped
        and counted, never an error."""
        wanted = set(item_ids) if item_ids is not None else None
        retried: list[str] = []
        skipped = capped = 0
        done = self.store.retry_counts()
        limit = self.retry_max()
        held = self._held_ids()
        for item_id in self._failed_ids():
            if wanted is not None and item_id not in wanted:
                continue
            if done.get(item_id, 0) >= limit:
                capped += 1
                continue
            if item_id in held:  # spec 029: a held warning is accepted one by one, never in bulk
                skipped += 1
                continue
            try:
                self.retry_item(item_id, actor=actor)
            except Refused:
                skipped += 1
                continue
            retried.append(item_id)
        self.store.audit(actor, "bulk_retry", None, retried=len(retried), skipped=skipped, capped=capped)
        return {"retried": len(retried), "skipped": skipped, "capped": capped, "item_ids": retried}

    def triage_info(self) -> dict[str, Any]:
        """Where the table lives and whether a switch of ``triage.surface`` still has to be moved by the next
        triage run (spec 023); for the banner of the triage page."""
        vault = self._vault()
        layout = vault.policy.layout
        return {
            "surface": vault.policy.triage_surface,
            "page": layout.triage_file,
            "data_file": layout.triage_data,
            "migration": vault.triage_migration_plan(),
            "proposals_dir": layout.proposals_dir,
        }

    def _decide(self, actor: str, action: str, item_id: str, **decision: Any) -> dict[str, Any]:
        try:
            row = self._vault().owner_decide(item_id, categories=self.category_map(), **decision)
        except VaultError as exc:
            self.store.audit(actor, f"{action}_refused", item_id, reason=str(exc))
            raise Refused(str(exc)) from None
        self.store.audit(actor, action, item_id, **{k: v for k, v in decision.items() if v is not None})
        return row

    def confirm_item(self, item_id: str, *, actor: str, confirm: bool = True) -> dict[str, Any]:
        return self._decide(actor, "confirm_item" if confirm else "unconfirm_item", item_id, confirm=confirm)

    def override_item(self, item_id: str, category: str, *, actor: str) -> dict[str, Any]:
        """An override: a configured category (an id, or a variant of an id or label, stored as the id) or
        a valid new-category name (stored as its slug: the next 06:00 run proposes the category, spec 022)."""
        if not category or not category.strip():
            raise Refused("an override needs a category")
        row = self._decide(actor, "override_item", item_id, override=category)
        self.collect_override_candidates()
        return row

    def comment_item(self, item_id: str, comment: str, *, actor: str) -> dict[str, Any]:
        """The owner's comment of one row (spec 023). Audited by length only, never by text."""
        try:
            row = self._vault().owner_comment(item_id, comment)
        except VaultError as exc:
            self.store.audit(actor, "comment_item_refused", item_id, reason=str(exc))
            raise Refused(str(exc)) from None
        self.store.audit(actor, "comment_item", item_id, chars=len(row.get("comment") or ""))
        self.collect_override_candidates()
        return row

    def bulk_confirm(
        self,
        *,
        actor: str,
        state: str | None = None,
        category: str | None = None,
        needs_owner: bool | None = None,
    ) -> dict[str, Any]:
        """Confirm the suggested category of every row of the filtered set that still waits for the owner
        and has a configured suggestion (spec 023). An image that needs the owner, a row with an override
        and a row without a valid suggestion are skipped; the same rules as a single confirm apply."""
        valid = self.categories()
        confirmed, skipped = [], 0
        for r in self.list_triage(state, category=category, needs_owner=needs_owner)["rows"]:
            try:
                unreviewed = is_unreviewed(ItemState(r["state"]), bool(r["confirm"]), r["override"])
            except ValueError:
                unreviewed = False
            if not unreviewed or r["needs_owner"] or r["ai_suggested_category"] not in valid:
                skipped += 1
                continue
            try:
                self._vault().owner_decide(r["item_id"], confirm=True, categories=self.category_map())
            except VaultError:
                skipped += 1
                continue
            confirmed.append(r["item_id"])
        self.store.audit(
            actor,
            "bulk_confirm",
            None,
            confirmed=len(confirmed),
            skipped=skipped,
            state=state,
            category=category,
            needs_owner=needs_owner,
        )
        return {"confirmed": len(confirmed), "skipped": skipped, "item_ids": confirmed}

    def item_image(self, item_id: str, *, max_side: int = 480) -> dict[str, Any]:
        """The thumbnail of an image row for the UI (spec 023): the file is read through ``read_image`` (name
        only, the read-only attachments folder, size limit, signature check, never a URL) and, with Pillow,
        decoded and re-encoded without metadata, like the bytes a vision model gets."""
        from pathlib import PurePosixPath

        from pkpipe.obsidian.errors import ImageRejected

        rows = {r["item_id"]: r for r in self._vault().read_triage()["rows"]}
        row = rows.get(item_id)
        if row is None:
            raise NotFound(f"unknown triage item '{item_id}'")
        resource = str(row["resource"])
        if row["kind"] != "image" or resource.startswith(("http://", "https://")):
            raise NotFound("this item has no local image")
        try:
            raw = self._vault().read_image(PurePosixPath(resource).name)
        except ImageRejected as exc:
            raise NotFound(f"image not available ({exc.code})") from None
        import base64

        data = base64.b64decode(raw["data_base64"])
        media_type = raw["media_type"]
        if self.image_preparer is not None:
            try:
                prepared = self.image_preparer.prepare(data, max_side=max_side, max_pixels=40_000_000)
                data, media_type = prepared.data, "image/jpeg"
            except ImageError:
                pass  # the original (it passed the signature check): better than no thumbnail
        return {"media_type": media_type, "data": data}

    def collect_override_candidates(self) -> dict[str, int]:
        """Spec 023 / design rule 6.4: an owner override that differs from the suggestion is kept, with the
        owner's comment as the reason, as an eval candidate in the private overlay
        (``<overlay>/evals/candidates/triage.jsonl``, else the data directory; never the public repo). The
        owner reviews candidates before they join a dataset. Idempotent: one line per item."""
        if self.candidates_dir is None or self.vault is None:
            return {}
        from pkpipe.adapters.security_log_files import upsert_eval_candidate

        counts: dict[str, int] = {}
        day = time.strftime("%Y-%m-%d", time.gmtime())
        for r in self.vault.read_triage()["rows"]:
            override = r.get("override")
            if not override or override == r["ai_suggested_category"]:
                continue
            case = {
                "id": f"override-{r['item_id']}",
                "source": "owner_override",
                "input": {"resource": r["resource"][:MAX_RESOURCE_CHARS], "kind": r.get("kind") or "note"},
                "expected": {"category": override},
                "ai_suggested": r["ai_suggested_category"],
                "reason": r.get("comment") or "",
                "day": day,
            }
            outcome = upsert_eval_candidate(
                self.candidates_dir / "triage.jsonl", {**case, "day": self._day(r, case)}
            )
            counts[outcome] = counts.get(outcome, 0) + 1
        return counts

    def _day(self, row: dict[str, Any], case: dict[str, Any]) -> str:
        """Keep the day a candidate was first collected (re-collecting must not rewrite the file)."""
        path = self.candidates_dir / "triage.jsonl" if self.candidates_dir else None
        if path is not None and path.is_file():
            import json

            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip() and json.loads(line).get("id") == case["id"]:
                    return str(json.loads(line).get("day") or case["day"])
        return str(case["day"])

    def release_quarantine(self, item_id: str, *, actor: str) -> dict[str, Any]:
        """Owner decision (spec 009 US3): the item is re-queued (``pending``, its note back in the INBOX, so
        the next triage run suggests a category again) and its text becomes a candidate case of the
        security eval dataset (``<overlay>/evals/candidates/security.jsonl``). The note text is never
        returned (an agent token may call this tool's sibling resources)."""
        try:
            result = self._vault().owner_release_quarantine(item_id)
        except VaultError as exc:
            self.store.audit(actor, "release_quarantine_refused", item_id, reason=str(exc))
            raise Refused(str(exc)) from None
        content = result.pop("content", None)
        flags = result.pop("flags", None) or []
        candidate = None
        if content and self.candidates_dir is not None:
            from pkpipe.adapters.security_log_files import append_eval_candidate
            from pkpipe.security.quarantine import release_candidate

            day = time.strftime("%Y-%m-%d", time.gmtime())
            case = release_candidate(item_id=item_id, text=content, flags=flags, day=day)
            append_eval_candidate(self.candidates_dir / "security.jsonl", case)
            candidate = case["id"]
        self.store.audit(
            actor,
            "release_quarantine",
            item_id,
            state=ItemState.PENDING.value,
            restored=result.get("restored"),
            eval_candidate=candidate,
        )
        return {**result, "requeued": True, "eval_candidate": candidate}

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
        return decision, {
            "stage": "gate",
            **decision.to_dict(),
            "metrics": gate_metrics(decision),
            "warnings": result.warnings,
        }

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
        latest = self._draft(draft_id)
        if latest["rev"] != draft["rev"]:  # edited in another tab while the evals ran: gate what is committed
            self.store.audit(actor, "publish_refused", draft_id, reason="draft changed during the gate")
            raise Refused("the draft changed while the eval gate ran; publish again to gate the new version")
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


def gate_metrics(decision: GateDecision) -> dict[str, dict[str, Any]]:
    """Measured value of every gated metric per suite (kept with the publish record, shown in the UI)."""
    if decision.run is None:
        return {}
    return {
        s.suite: {
            "status": s.status.value,
            **{g.metric: {"value": g.value, "op": g.op, "threshold": g.threshold} for g in s.gate},
        }
        for s in decision.run.suites
    }


def default_vault(vault_dir: Path | None, policy: Any = None) -> Vault | None:
    """The UI's vault: with the policy of the run configuration when given (layout, ``triage.surface``),
    else the defaults."""
    if vault_dir is None and policy is None:
        return None
    if policy is not None:
        return Vault(policy)
    from pkpipe.obsidian.policy import VaultPolicy

    return Vault(VaultPolicy(vault_dir=vault_dir))
