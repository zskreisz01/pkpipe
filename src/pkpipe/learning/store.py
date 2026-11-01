"""SQLite job store of the gateway: jobs survive a restart (polling resumes mid-generation)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .models import JobStatus, NotebookJob

_SCHEMA = """
create table if not exists notebook_jobs (
    job_id text primary key,
    item_id text not null,
    nb_id text,
    status text not null,
    started_at real not null,
    data text not null
);
create index if not exists notebook_jobs_item on notebook_jobs(item_id);
create index if not exists notebook_jobs_nb on notebook_jobs(nb_id);
"""


class JobStore:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def save(self, job: NotebookJob) -> None:
        with self._lock:
            self._conn.execute(
                "insert or replace into notebook_jobs(job_id, item_id, nb_id, status, started_at, data)"
                " values (?,?,?,?,?,?)",
                (job.job_id, job.item_id, job.nb_id, job.status.value, job.started_at, job.model_dump_json()),
            )

    def _load(self, sql: str, *args) -> list[NotebookJob]:
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [NotebookJob.model_validate_json(r[0]) for r in rows]

    def get(self, job_id: str) -> NotebookJob | None:
        found = self._load("select data from notebook_jobs where job_id=?", job_id)
        return found[0] if found else None

    def by_notebook(self, nb_id: str) -> NotebookJob | None:
        found = self._load(
            "select data from notebook_jobs where nb_id=? order by started_at desc limit 1", nb_id
        )
        return found[0] if found else None

    def active_for_item(self, item_id: str) -> NotebookJob | None:
        """The newest job of the item that is generating or ready (not failed)."""
        found = self._load(
            "select data from notebook_jobs where item_id=? and status != ? order by started_at desc limit 1",
            item_id,
            JobStatus.FAILED.value,
        )
        return found[0] if found else None

    def list(self, status: JobStatus | None = None) -> list[NotebookJob]:
        if status:
            return self._load(
                "select data from notebook_jobs where status=? order by started_at", status.value
            )
        return self._load("select data from notebook_jobs order by started_at")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
