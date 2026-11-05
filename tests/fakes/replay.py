"""ReplayModel: a ModelPort that answers from responses recorded against the real model (spec 002).

Recordings live in ``tests/fixtures/model/*.json`` (written by ``tests/support/record_model.py`` against LM
Studio). Used by replay-based tests and evals so they run without a model server.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pkpipe.ports import ModelError

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "model"


def load_recordings(directory: Path = FIXTURES) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]


class ReplayModel:
    def __init__(self, recordings: list[dict[str, Any]] | None = None):
        self.recordings = load_recordings() if recordings is None else recordings
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None = None,
        profile: str = "default",
        images=None,
    ) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user, "json_schema": json_schema, "profile": profile})
        for rec in self.recordings:
            req = rec["request"]
            if req["system"] == system and req["user"] == user and req["json_schema"] == json_schema:
                return json.loads(json.dumps(rec["data"]))
        raise ModelError(
            f"no recording for this request (user={user[:60]!r}); re-run tests/support/record_model.py"
        )
