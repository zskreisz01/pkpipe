"""Eval entities (spec 013 data-model): EvalCase, CaseResult, SuiteReport, EvalRun, GateCheck."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Level(IntEnum):
    """The five eval levels of docs/evaluation.md."""

    DETERMINISTIC = 1
    STEP = 2
    FLOW = 3
    SECURITY = 4
    PRODUCTION = 5


class Source(StrEnum):
    """Where a case comes from: written by hand, an owner override, a quarantine release, or a golden item."""

    SYNTHETIC = "synthetic"
    OVERRIDE = "override"
    RELEASE = "release"
    GOLDEN = "golden"


class EvalCase(BaseModel):
    """One dataset entry (a line of a ``.jsonl`` dataset or one flow case file)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    suite: str = Field(min_length=1)
    input: dict[str, Any]
    expected: dict[str, Any] | None = None
    source: Source = Source.SYNTHETIC
    description: str | None = None


class Status(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    #: the suite could not run (no dataset, judge unavailable, ...): never counts as passed
    SKIPPED = "skipped"
    ERROR = "error"


class CaseResult(BaseModel):
    case_id: str
    passed: bool
    expected: Any = None
    actual: Any = None
    error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class GateCheck(BaseModel):
    metric: str
    op: str  # ">=" or "<="
    threshold: float
    value: float | None
    passed: bool


class SuiteReport(BaseModel):
    suite: str
    level: Level
    status: Status
    metrics: dict[str, Any] = Field(default_factory=dict)
    gate: list[GateCheck] = Field(default_factory=list)
    cases: list[CaseResult] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    reason: str | None = None  # why skipped / errored

    @property
    def ok(self) -> bool:
        return self.status is Status.PASSED

    def failures(self) -> list[str]:
        if self.status in (Status.SKIPPED, Status.ERROR):
            return [f"{self.suite}: {self.status.value} ({self.reason})"]
        return [
            f"{self.suite}: {g.metric} = {_fmt(g.value)} (gate {g.op} {_fmt(g.threshold)})"
            for g in self.gate
            if not g.passed
        ]


class EvalRun(BaseModel):
    """One ``pkpipe eval`` invocation; ``run_id`` is the ``eval.run_id`` on every span of the run."""

    run_id: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    config_version: str
    mode: str  # live | replay | record | auto
    model: str | None = None
    judge: str | None = None
    suites: list[SuiteReport] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.suites) and all(s.ok for s in self.suites)

    def suite(self, name: str) -> SuiteReport:
        for s in self.suites:
            if s.suite == name:
                return s
        raise KeyError(name)

    def failures(self) -> list[str]:
        return [f for s in self.suites for f in s.failures()]


def _fmt(v: float | None) -> str:
    if v is None:
        return "missing"
    return f"{v:.3f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
