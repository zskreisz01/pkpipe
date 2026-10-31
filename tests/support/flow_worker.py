"""Subprocess worker for the restart test (spec 005 US2 / SC-002).

``python tests/support/flow_worker.py --db app.db --pack pack/ --work work/ start|resume``

Runs the pack's ``batch`` flow with the real SQLite run store and checkpointer. Tools are file based so the
test (another process) can observe and steer them: ``jobs:status`` logs every poll and reports ready once
``<work>/<job>.ready`` exists; ``jobs:finish`` appends ``"<job> <tag>"`` to ``<work>/done.log``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pkpipe.adapters.state_sqlite import SqliteRunStore, open_checkpointer  # noqa: E402
from pkpipe.engine.registry import load_effective  # noqa: E402
from pkpipe.engine.runs import RunService  # noqa: E402
from pkpipe.engine.runtime_defaults import NullTracer, SystemClock  # noqa: E402
from pkpipe.steps import StepPorts  # noqa: E402
from tests.fakes import FakeTools  # noqa: E402


def _append(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()


def build(db: Path, pack: Path, work: Path) -> RunService:
    def status(job: str) -> dict:
        _append(work / "polls.log", job)
        return {"job": job, "ready": (work / f"{job}.ready").exists()}

    def finish(job: str, tag: str) -> dict:
        _append(work / "done.log", f"{job} {tag}")
        return {"job": job}

    tools = FakeTools(
        {
            "jobs:list": lambda: {"items": [{"id": j} for j in ("a", "b", "c")]},
            "jobs:status": status,
            "jobs:finish": finish,
        }
    )
    return RunService(
        store=SqliteRunStore(db),
        checkpointer=open_checkpointer(db),
        config=lambda: load_effective(pack, None, strict=True).snapshot(),
        ports_for=lambda snapshot: StepPorts(clock=SystemClock(), tracer=NullTracer(), tools=tools),
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--pack", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("mode", choices=["start", "resume"])
    a = p.parse_args()
    svc = build(Path(a.db), Path(a.pack), Path(a.work))
    outcomes = [svc.start("batch", run_id="restart-run")] if a.mode == "start" else svc.resume_incomplete()
    print(json.dumps([o.run.record() for o in outcomes]))
    return 0 if all(o.ok for o in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
