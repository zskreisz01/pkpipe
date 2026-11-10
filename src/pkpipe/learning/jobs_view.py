"""A read-only view of the gateway's job store for the pipeline overview (spec 033).

The learning gateway is its own process; the UI and the flows only need to know which notebooks wait for a
quota window or are being generated, and whether a finished one has its podcast. The job database is opened
read only and only ids, status words and times are returned. A missing or unreadable store is an empty view.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import JobStatus


def read_jobs(path: Path | str) -> dict[str, dict[str, Any]]:
    """``{item id: newest job}`` with ``status``, ``retry_after``, ``started_at``, ``link``, ``audio``."""
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=2)
        try:
            rows = conn.execute(
                "select item_id, status, data from notebook_jobs order by started_at"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item_id, status, raw in rows:
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
        if status == JobStatus.FAILED.value and item_id in out:
            continue  # a failed retry never hides the notebook that is still usable
        out[str(item_id)] = {
            "status": str(status),
            "retry_after": data.get("retry_after"),
            "started_at": data.get("started_at"),
            "link": data.get("link"),
            "audio": bool(data.get("audio_path")),
        }
    return out
