"""SQLite tables of the config UI and app MCP (spec 014): drafts, publishes and the owner-action audit log.

Same app DB file as the run table (WAL mode, one short-lived connection per call).
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pkpipe.adapters.state_sqlite import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    draft_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('open', 'published', 'discarded')),
    rev INTEGER NOT NULL,
    files TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publishes (
    publish_id TEXT PRIMARY KEY,
    draft_id TEXT,
    commit_sha TEXT NOT NULL,
    parent_sha TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('publish', 'rollback')),
    files TEXT NOT NULL,
    summary TEXT NOT NULL,
    decision TEXT NOT NULL,
    reverted_by TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    run_id TEXT PRIMARY KEY,
    flow_id TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    files INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('kept', 'restored', 'pruned')),
    reverted INTEGER NOT NULL DEFAULT 0,
    restored_at TEXT
);
CREATE TABLE IF NOT EXISTS image_cache (
    sha256 TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (sha256, model, prompt_version)
);
CREATE TABLE IF NOT EXISTS image_timing (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    model TEXT NOT NULL,
    seconds REAL NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS image_timing_model ON image_timing (model, seq);
CREATE TABLE IF NOT EXISTS item_retries (
    item_id TEXT PRIMARY KEY,
    retries INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS item_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    item_id TEXT NOT NULL,
    run_id TEXT,
    flow_id TEXT,
    step TEXT,
    kind TEXT NOT NULL,
    outcome TEXT,
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS item_events_item ON item_events (item_id, seq);
CREATE TABLE IF NOT EXISTS classifier_reviews (
    item_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    evidence TEXT NOT NULL,
    held_at TEXT NOT NULL,
    waived_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_attempts (
    item_id TEXT PRIMARY KEY,
    requested TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class RevisionConflict(Exception):
    """The draft changed since the caller read it (optimistic lock)."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AppStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextlib.contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # -- drafts ---------------------------------------------------------------------------------------------
    @staticmethod
    def _draft(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["files"] = json.loads(d["files"])
        return d

    def create_draft(self) -> dict[str, Any]:
        draft_id, now = uuid.uuid4().hex[:12], _now()
        with self._conn() as c:
            c.execute("INSERT INTO drafts VALUES (?, 'open', 0, '{}', ?, ?)", (draft_id, now, now))
        return self.get_draft(draft_id)  # type: ignore[return-value]

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM drafts WHERE draft_id = ?", (draft_id,)).fetchone()
        return self._draft(row) if row else None

    def list_drafts(self, status: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM drafts", []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        with self._conn() as c:
            return [self._draft(r) for r in c.execute(sql + " ORDER BY created_at", args)]

    def save_draft(
        self, draft_id: str, files: dict[str, Any], *, expected_rev: int | None, status: str | None = None
    ) -> dict[str, Any]:
        """Replace the draft's files. With ``expected_rev`` the write fails if another save came first."""
        sql = "UPDATE drafts SET files = ?, rev = rev + 1, updated_at = ?"
        args: list[Any] = [json.dumps(files, sort_keys=True), _now()]
        if status:
            sql += ", status = ?"
            args.append(status)
        sql += " WHERE draft_id = ?"
        args.append(draft_id)
        if expected_rev is not None:
            sql += " AND rev = ?"
            args.append(expected_rev)
        with self._conn() as c:
            if c.execute(sql, args).rowcount == 0:
                if c.execute("SELECT 1 FROM drafts WHERE draft_id = ?", (draft_id,)).fetchone() is None:
                    raise KeyError(f"unknown draft '{draft_id}'")
                raise RevisionConflict("the draft was changed elsewhere; reload it")
        return self.get_draft(draft_id)  # type: ignore[return-value]

    # -- publishes ------------------------------------------------------------------------------------------
    @staticmethod
    def _publish(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["files"] = json.loads(d["files"])
        d["decision"] = json.loads(d["decision"])
        return d

    def add_publish(
        self,
        *,
        draft_id: str | None,
        commit_sha: str,
        parent_sha: str | None,
        kind: str,
        files: list[str],
        summary: str,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        publish_id = uuid.uuid4().hex[:12]
        with self._conn() as c:
            c.execute(
                "INSERT INTO publishes VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    publish_id,
                    draft_id,
                    commit_sha,
                    parent_sha,
                    kind,
                    json.dumps(files),
                    summary,
                    json.dumps(decision, default=str),
                    _now(),
                ),
            )
        return self.get_publish(publish_id)  # type: ignore[return-value]

    def get_publish(self, publish_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM publishes WHERE publish_id = ?", (publish_id,)).fetchone()
        return self._publish(row) if row else None

    def list_publishes(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [self._publish(r) for r in c.execute("SELECT * FROM publishes ORDER BY created_at DESC")]

    def mark_reverted(self, publish_id: str, by: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE publishes SET reverted_by = ? WHERE publish_id = ?", (by, publish_id))

    # -- vault snapshots (spec 019 FR-008): the index of ``$PKPIPE_DATA_DIR/snapshots/`` ---------------------
    SNAPSHOT_FIELDS = frozenset(
        {"flow_id", "taken_at", "bytes", "files", "status", "reverted", "restored_at"}
    )

    def snapshot_upsert(self, row: dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO snapshots "
                "(run_id, flow_id, taken_at, bytes, files, status, reverted, restored_at) "
                "VALUES (:run_id, :flow_id, :taken_at, :bytes, :files, :status, :reverted, :restored_at) "
                "ON CONFLICT(run_id) DO UPDATE SET flow_id = excluded.flow_id, taken_at = excluded.taken_at, "
                "bytes = excluded.bytes, files = excluded.files, status = excluded.status",
                {"status": "kept", "reverted": 0, "restored_at": None, **row},
            )

    def snapshot_get(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM snapshots WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def snapshot_list(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM snapshots ORDER BY taken_at DESC, run_id")]

    def snapshot_update(self, run_id: str, **fields: Any) -> None:
        bad = set(fields) - self.SNAPSHOT_FIELDS
        if bad:
            raise ValueError(f"snapshot fields {sorted(bad)} cannot be set")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE snapshots SET {sets} WHERE run_id = ?", [*fields.values(), run_id])

    # -- image input (spec 021 FR-012, FR-014): ImageStorePort ---------------------------------------------
    def cache_get(self, sha256: str, model: str, prompt_version: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT description FROM image_cache WHERE sha256 = ? AND model = ? AND prompt_version = ?",
                (sha256, model, prompt_version),
            ).fetchone()
        return json.loads(row["description"]) if row else None

    def cache_get_latest(self, sha256: str, model: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT description FROM image_cache WHERE sha256 = ? AND model = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (sha256, model),
            ).fetchone()
        return json.loads(row["description"]) if row else None

    def cache_put(self, sha256: str, model: str, prompt_version: str, description: dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO image_cache (sha256, model, prompt_version, description, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (sha256, model, prompt_version, json.dumps(description, ensure_ascii=False), _now()),
            )

    def record_timing(self, run_id: str, sha256: str, model: str, seconds: float) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO image_timing (run_id, sha256, model, seconds, at) VALUES (?, ?, ?, ?, ?)",
                (run_id, sha256, model, float(seconds), _now()),
            )

    def recent_seconds(self, model: str, limit: int = 50) -> list[float]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT seconds FROM image_timing WHERE model = ? ORDER BY seq DESC LIMIT ?", (model, limit)
            ).fetchall()
        return [float(r["seconds"]) for r in rows]

    # -- new-category proposals (spec 022 FR-009): ProposalAttemptsPort -------------------------------
    def get(self, item_id: str, requested: str) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT attempts FROM proposal_attempts WHERE item_id = ? AND requested = ?",
                (item_id, requested),
            ).fetchone()
        return int(row["attempts"]) if row else 0

    def bump(self, item_id: str, requested: str) -> int:
        count = self.get(item_id, requested) + 1
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO proposal_attempts (item_id, requested, attempts, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (item_id, requested, count, _now()),
            )
        return count

    def clear(self, item_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM proposal_attempts WHERE item_id = ?", (item_id,))

    # -- owner retries of failed items (spec 027) -----------------------------------------------------------
    def retry_count(self, item_id: str) -> int:
        with self._conn() as c:
            row = c.execute("SELECT retries FROM item_retries WHERE item_id = ?", (item_id,)).fetchone()
        return int(row["retries"]) if row else 0

    def retry_counts(self) -> dict[str, int]:
        with self._conn() as c:
            rows = c.execute("SELECT item_id, retries FROM item_retries").fetchall()
        return {r["item_id"]: int(r["retries"]) for r in rows}

    def bump_retry(self, item_id: str) -> int:
        count = self.retry_count(item_id) + 1
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO item_retries (item_id, retries, updated_at) VALUES (?, ?, ?)",
                (item_id, count, _now()),
            )
        return count

    # -- classifier review (spec 029): ClassifierReviewPort --------------------------------------------
    def hold(self, item_id: str, url: str, evidence: dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO classifier_reviews (item_id, url, evidence, held_at, waived_at) "
                "VALUES (?, ?, ?, ?, NULL) ON CONFLICT(item_id) DO UPDATE SET url = excluded.url, "
                "evidence = excluded.evidence, held_at = excluded.held_at",
                (item_id, url[:500], json.dumps(evidence, ensure_ascii=False), _now()),
            )

    @staticmethod
    def _review_row(row: Any) -> dict[str, Any]:
        return {
            "item_id": row["item_id"],
            "url": row["url"],
            "evidence": json.loads(row["evidence"]),
            "held_at": row["held_at"],
            "waived": row["waived_at"] is not None,
        }

    def review_of(self, item_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM classifier_reviews WHERE item_id = ?", (item_id,)).fetchone()
        return self._review_row(row) if row else None

    def held(self) -> dict[str, dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM classifier_reviews").fetchall()
        return {r["item_id"]: self._review_row(r) for r in rows}

    def waive(self, item_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE classifier_reviews SET waived_at = ? WHERE item_id = ?", (_now(), item_id))

    def waived(self, item_id: str) -> bool:
        row = self.review_of(item_id)
        return bool(row and row["waived"])

    # -- item event log (spec 030): ItemEventPort ------------------------------------------------------
    def record(
        self,
        item_id: str,
        kind: str,
        *,
        run_id: str | None = None,
        flow_id: str | None = None,
        step: str | None = None,
        outcome: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO item_events (at, item_id, run_id, flow_id, step, kind, outcome, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _now(),
                    item_id,
                    run_id,
                    flow_id,
                    step,
                    kind,
                    outcome,
                    json.dumps(detail or {}, ensure_ascii=False, default=str),
                ),
            )

    def events_of(self, item_id: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM item_events WHERE item_id = ? ORDER BY seq LIMIT ?", (item_id, limit)
            ).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    def recent_item_ids(self, limit: int = 50) -> list[str]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT item_id FROM item_events GROUP BY item_id ORDER BY MAX(seq) DESC LIMIT ?", (limit,)
            ).fetchall()
        return [r["item_id"] for r in rows]

    def audit_of(self, item_id: str, limit: int = 200) -> list[dict[str, Any]]:
        """The owner actions on one item (the audit rows that name it as target), oldest first."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM audit WHERE target = ? ORDER BY seq LIMIT ?", (item_id, limit)
            ).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    # -- audit ----------------------------------------------------------------------------------------------
    def audit(self, actor: str, action: str, target: str | None = None, **detail: Any) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO audit (at, actor, action, target, detail) VALUES (?, ?, ?, ?, ?)",
                (_now(), actor, action, target, json.dumps(detail, default=str)),
            )

    def audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]
