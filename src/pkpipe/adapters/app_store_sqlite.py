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
