"""Evaluation framework and gates (spec 013): five levels, datasets, code-first scoring, an LLM judge with
calibration, cassettes for CI, and the publish gate used by the config UI (spec 014).

Public API::

    from pkpipe.evals import EvalRunner, EvalPaths, PublishGate, CassetteModel

    run = EvalRunner(snapshot, model=model).run(["triage"])      # EvalRun with SuiteReports
    decision = PublishGate(model=model).check(current, draft)    # GateDecision(allowed, failures, ...)
"""

from pkpipe.evals.cassette import CassetteMiss, CassetteModel
from pkpipe.evals.datasets import DatasetError, EvalPaths
from pkpipe.evals.gate import GateDecision, PublishGate, affected_suites, threshold_regressions
from pkpipe.evals.judge import Judge, JudgeScore, JudgeUnavailable
from pkpipe.evals.models import CaseResult, EvalCase, EvalRun, GateCheck, Level, Source, Status, SuiteReport
from pkpipe.evals.report import to_json, to_markdown, write_reports
from pkpipe.evals.runner import EvalRunner, evals_config, profile_identities
from pkpipe.evals.suites import DEFAULT_SUITES, SUITES, resolve_suites

__all__ = [
    "DEFAULT_SUITES",
    "SUITES",
    "CaseResult",
    "CassetteMiss",
    "CassetteModel",
    "DatasetError",
    "EvalCase",
    "EvalPaths",
    "EvalRun",
    "EvalRunner",
    "GateCheck",
    "GateDecision",
    "Judge",
    "JudgeScore",
    "JudgeUnavailable",
    "Level",
    "PublishGate",
    "Source",
    "Status",
    "SuiteReport",
    "affected_suites",
    "evals_config",
    "profile_identities",
    "resolve_suites",
    "threshold_regressions",
    "to_json",
    "to_markdown",
    "write_reports",
]
