"""Run service: start, resume and record flow runs (spec 005 US2, FR-004, T006-T007).

* A run gets a UUID that is also the LangGraph ``thread_id``; the checkpointer persists every step.
* The run pins the config: the snapshot (JSON + assets) is stored under its version when the run starts and
  every resume compiles from that stored snapshot, never from the config on disk.
* Ports are built per snapshot (``ports_for(snapshot)``), so model profiles and tool servers are pinned too.
* After a crash, :meth:`RunService.resume_incomplete` continues every ``running``/``waiting`` run from its
  last checkpoint; finished steps are not executed again.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from pkpipe.engine.compiler import FlowCompiler
from pkpipe.engine.snapshot import ConfigSnapshot
from pkpipe.ports import RunStorePort
from pkpipe.steps import RunInfo, StepPorts, StepRegistry

log = logging.getLogger("pkpipe.runs")


class RunStatus(StrEnum):
    RUNNING = "running"
    WAITING = "waiting"
    DONE = "done"
    FAILED = "failed"


class Trigger(StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"
    MCP = "mcp"
    UI = "ui"


ACTIVE = (RunStatus.RUNNING, RunStatus.WAITING)


class Run(BaseModel):
    run_id: str
    flow_id: str
    config_version: str
    trigger: Trigger
    status: RunStatus
    started_at: datetime
    ended_at: datetime | None = None
    error: str | None = None

    def record(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@dataclass
class RunOutcome:
    run: Run
    state: dict[str, Any] | None = None
    error: BaseException | None = None

    @property
    def ok(self) -> bool:
        return self.run.status is RunStatus.DONE

    @property
    def outputs(self) -> dict[str, Any]:
        state = self.state or {}
        return {**(state.get("item_outputs") or {}), **(state.get("outputs") or {})}


def _now() -> datetime:
    return datetime.now(UTC)


class RunService:
    def __init__(
        self,
        *,
        store: RunStorePort,
        checkpointer: Any,
        config: Callable[[], ConfigSnapshot],
        ports_for: Callable[[ConfigSnapshot], StepPorts],
        registry: StepRegistry | None = None,
    ):
        self.store = store
        self.checkpointer = checkpointer
        self.config = config
        self.ports_for = ports_for
        self.registry = registry
        self._lock = threading.Lock()

    # -- queries --------------------------------------------------------------------------------------------
    def get(self, run_id: str) -> Run | None:
        rec = self.store.get_run(run_id)
        return Run.model_validate(rec) if rec else None

    def list(self, *, status: str | None = None, flow_id: str | None = None) -> list[Run]:
        return [Run.model_validate(r) for r in self.store.list_runs(status=status, flow_id=flow_id)]

    # -- commands -------------------------------------------------------------------------------------------
    def start(
        self,
        flow_id: str,
        *,
        trigger: Trigger | str = Trigger.MANUAL,
        inputs: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> RunOutcome:
        """Start ``flow_id`` on the current config (pinned from here on) and run it to the end."""
        snapshot = self.config()
        snapshot.flow(flow_id)  # unknown flow: KeyError before anything is recorded
        self.store.save_snapshot(snapshot.version, snapshot.data, snapshot.assets)
        run = Run(
            run_id=run_id or str(uuid.uuid4()),
            flow_id=flow_id,
            config_version=snapshot.version,
            trigger=Trigger(trigger),
            status=RunStatus.RUNNING,
            started_at=_now(),
        )
        self.store.create_run(run.record())
        graph_input = {"input": dict(inputs or {}), "outputs": {}, "item_outputs": {}}
        return self._execute(run, snapshot, graph_input)

    def resume(self, run_id: str) -> RunOutcome:
        """Continue a run from its last checkpoint with its pinned config version."""
        run = self.get(run_id)
        if run is None:
            raise KeyError(f"unknown run '{run_id}'")
        if run.status not in ACTIVE:
            raise ValueError(f"run '{run_id}' is {run.status.value}; only running or waiting runs resume")
        stored = self.store.load_snapshot(run.config_version)
        if stored is None:
            raise LookupError(f"config snapshot {run.config_version[:12]} of run '{run_id}' is missing")
        data, assets = stored
        snapshot = ConfigSnapshot(run.config_version, data, assets)
        if self.checkpointer.get_tuple({"configurable": {"thread_id": run.run_id}}) is None:
            # the process stopped between recording the run and its first checkpoint: nothing ran yet and
            # the inputs are gone, so the run cannot continue; the next trigger starts a new one
            return self._fail(run, LookupError("no checkpoint: the run stopped before its first step"))
        self._set_status(run.run_id, RunStatus.RUNNING)
        return self._execute(run, snapshot, None)

    def resume_incomplete(self) -> list[RunOutcome]:
        """Resume every run left ``running`` or ``waiting`` (after a crash or restart)."""
        outcomes = []
        for status in ACTIVE:
            for run in self.list(status=status.value):
                log.info("resuming run %s (%s)", run.run_id, run.flow_id)
                try:
                    outcomes.append(self.resume(run.run_id))
                except LookupError as exc:  # snapshot gone: record it, keep resuming the others
                    outcomes.append(self._fail(run, exc))
        return outcomes

    # -- internals ------------------------------------------------------------------------------------------
    def _set_status(self, run_id: str, status: RunStatus | str) -> None:
        with self._lock:
            self.store.update_run(run_id, status=RunStatus(status).value)

    def _fail(self, run: Run, exc: BaseException) -> RunOutcome:
        ended = _now()
        message = f"{type(exc).__name__}: {exc}"[:2000]
        with self._lock:
            self.store.update_run(
                run.run_id, status=RunStatus.FAILED.value, ended_at=ended.isoformat(), error=message
            )
        log.info("run %s (%s) failed: %s", run.run_id, run.flow_id, message)
        failed = run.model_copy(update={"status": RunStatus.FAILED, "ended_at": ended, "error": message})
        return RunOutcome(failed, None, exc)

    def _execute(self, run: Run, snapshot: ConfigSnapshot, graph_input: dict[str, Any] | None) -> RunOutcome:
        flow = snapshot.flow(run.flow_id)
        ports = self.ports_for(snapshot)
        info = RunInfo(run.run_id, run.flow_id, run.trigger.value, snapshot.version)

        def on_status(status: str) -> None:
            self._set_status(run.run_id, status)

        compiler = FlowCompiler(snapshot, ports, info, registry=self.registry, on_status=on_status)
        config = {
            "configurable": {"thread_id": run.run_id},
            "recursion_limit": flow.max_steps,
            "max_concurrency": flow.max_parallel,
        }
        try:
            graph = compiler.compile(run.flow_id, checkpointer=self.checkpointer)
            with ports.tracer.flow(
                run.flow_id,
                trigger=run.trigger.value,
                config_version=snapshot.version,
                data_classes=tuple(flow.data_classes),
            ):
                state = graph.invoke(graph_input, config)
        except Exception as exc:  # noqa: BLE001 - recorded on the run, returned to the caller
            return self._fail(run, exc)
        ended = _now()
        with self._lock:
            self.store.update_run(
                run.run_id, status=RunStatus.DONE.value, ended_at=ended.isoformat(), error=None
            )
        done = run.model_copy(update={"status": RunStatus.DONE, "ended_at": ended, "error": None})
        return RunOutcome(done, state, None)
