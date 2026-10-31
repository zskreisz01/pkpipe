"""SQLite app DB: run table, pinned config snapshots and the LangGraph checkpointer (spec 005 FR-004).

One file (default ``$PKPIPE_DATA_DIR/pkpipe.db``) holds the ``runs`` and ``config_snapshots`` tables, the
LangGraph checkpoint tables and the APScheduler job store. WAL mode lets the scheduler, the API and a CLI run
use it at the same time.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

RUN_FIELDS = ("run_id", "flow_id", "config_version", "trigger", "status", "started_at", "ended_at", "error")
UPDATABLE = frozenset({"status", "ended_at", "error"})
_INSERT_RUN = f"INSERT INTO runs ({', '.join(RUN_FIELDS)}) VALUES ({', '.join('?' * len(RUN_FIELDS))})"
_INSERT_SNAPSHOT = (
    "INSERT OR IGNORE INTO config_snapshots (version, data, assets, created_at) VALUES (?, ?, ?, ?)"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    flow_id TEXT NOT NULL,
    config_version TEXT NOT NULL,
    trigger TEXT NOT NULL CHECK (trigger IN ('schedule', 'manual', 'mcp', 'ui')),
    status TEXT NOT NULL CHECK (status IN ('running', 'waiting', 'done', 'failed')),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS runs_status ON runs (status);
CREATE INDEX IF NOT EXISTS runs_flow ON runs (flow_id, started_at);
CREATE TABLE IF NOT EXISTS config_snapshots (
    version TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    assets TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


class SqliteRunStore:
    """Implements :class:`pkpipe.ports.RunStorePort`. A short-lived connection per call (thread safe)."""

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

    def create_run(self, record: dict[str, Any]) -> None:
        values = [record.get(f) for f in RUN_FIELDS]
        with self._conn() as c:
            c.execute(_INSERT_RUN, values)

    def update_run(self, run_id: str, **fields: Any) -> None:
        bad = set(fields) - UPDATABLE
        if bad:
            raise ValueError(f"run fields {sorted(bad)} cannot be changed")
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._conn() as c:
            cur = c.execute(f"UPDATE runs SET {assignments} WHERE run_id = ?", [*fields.values(), run_id])
            if cur.rowcount == 0:
                raise KeyError(f"unknown run '{run_id}'")

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self, *, status: str | None = None, flow_id: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM runs WHERE 1=1", []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if flow_id:
            sql += " AND flow_id = ?"
            args.append(flow_id)
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql + " ORDER BY started_at", args)]

    def save_snapshot(self, version: str, data: dict[str, Any], assets: dict[str, str]) -> None:
        with self._conn() as c:
            created = datetime.now(UTC).isoformat()
            c.execute(
                _INSERT_SNAPSHOT,
                (version, json.dumps(data, sort_keys=True), json.dumps(assets, sort_keys=True), created),
            )

    def load_snapshot(self, version: str) -> tuple[dict[str, Any], dict[str, str]] | None:
        with self._conn() as c:
            sql = "SELECT data, assets FROM config_snapshots WHERE version = ?"
            row = c.execute(sql, (version,)).fetchone()
        return (json.loads(row["data"]), json.loads(row["assets"])) if row else None


def open_checkpointer(path: Path) -> SqliteSaver:
    """LangGraph SQLite checkpointer on the app DB (its own long-lived connection)."""
    saver = SqliteSaver(connect(Path(path)))
    saver.setup()
    return saver
