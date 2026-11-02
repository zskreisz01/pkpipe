"""SystemProbePort fake: scripted snapshots, plus a builder for a healthy machine to change one thing at a
time."""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Iterable
from typing import Any

from pkpipe.domain.system import (
    LlmModel,
    LlmServerState,
    MemoryState,
    ProcessUsage,
    SwapState,
    SystemSnapshot,
    ThermalState,
)

GiB = 1024**3
MODEL, JUDGE = "model-under-test", "judge-model"


def healthy_snapshot(**changes: Any) -> SystemSnapshot:
    """A 32 GiB, 10-CPU machine with nothing wrong and both models loaded with 4 parallel predictions.
    ``changes`` replace top-level fields; ``memory=dict(...)`` / ``swap=`` / ``thermal=`` / ``llm=`` dicts
    replace fields of those parts."""
    base = SystemSnapshot(
        taken_at=1_800_000_000.0,
        platform="darwin-arm64",
        cpu_count=10,
        load_avg=(2.0, 2.0, 2.0),
        memory=MemoryState(32 * GiB, available_bytes=16 * GiB, free_bytes=4 * GiB, pressure="normal"),
        swap=SwapState(8 * GiB, 1 * GiB),
        thermal=ThermalState(level="nominal", cpu_speed_limit=None),
        processes=(ProcessUsage("llm-server", 12 * GiB), ProcessUsage("editor", 1 * GiB)),
        llm=LlmServerState(
            "http://127.0.0.1:1234/v1",
            True,
            tuple(
                LlmModel(m, loaded=True, parallel=4, context_length=32768, status="idle", queued=0)
                for m in (MODEL, JUDGE)
            ),
            ("openai", "lmstudio-rest", "lms"),
        ),
    )
    for part in ("memory", "swap", "thermal", "llm"):
        if isinstance(changes.get(part), dict):
            changes[part] = dataclasses.replace(getattr(base, part), **changes[part])
    return dataclasses.replace(base, **changes)


class FakeSystemProbe:
    """Returns the scripted snapshots in order (the last one repeats); ``set`` replaces the script."""

    def __init__(self, snapshots: SystemSnapshot | Iterable[SystemSnapshot] | None = None):
        self._lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []
        self.set(snapshots if snapshots is not None else healthy_snapshot())

    def set(self, snapshots: SystemSnapshot | Iterable[SystemSnapshot]) -> None:
        with self._lock:
            self._script = [snapshots] if isinstance(snapshots, SystemSnapshot) else list(snapshots)

    def snapshot(self, *, include_llm: bool = True) -> SystemSnapshot:
        with self._lock:
            self.calls.append({"include_llm": include_llm})
            snap = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        return snap if include_llm else dataclasses.replace(snap, llm=None)
