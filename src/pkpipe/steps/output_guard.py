"""``output_guard`` step (spec 009 FR-001 to FR-003, BPMN element "Output guard").

Checks session outputs that derive from external content before anything is written to the vault: the
run's canary (deterministic), heuristics and the classifier of ``security.classifier`` (Qwen3Guard harm
categories and jailbreaks, DeBERTa injection score). The decision is code (:mod:`pkpipe.security.guard`).

A flag sets ``item.flagged`` and ``item.security_flags`` and the flow routes the item to the
``handle_security_flag`` subflow (quarantine), so nothing of the flagged output reaches a vault write; the
other items of a fan-out continue (FR-003). The step's own output holds flag names, detectors, reason codes
and scores only, never the checked text.

Validation (:func:`pkpipe.steps.llm.guarded_session_errors`): a session with ``canary: true`` (the marker of
a session over external content) may only be followed by an ``output_guard`` that checks it, so no flow can
write such an output unchecked.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from pkpipe.security.guard import check_output
from pkpipe.steps.base import StepConfig, StepContext, StepError, StepExecutor, StepResult, register_step
from pkpipe.steps.llm import step_canary

CLASSIFIER_PORT = "classifier"


class OutputGuardConfig(StepConfig):
    input: list[str] = Field(
        min_length=1, description="References of the outputs to check (node ids or paths, e.g. 'summarize')"
    )
    canary: bool = Field(default=True, description="Check the run's canary (security.canary)")

    @field_validator("input", mode="before")
    @classmethod
    def _one_or_many(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v


@register_step
class OutputGuardStep(StepExecutor):
    type = "output_guard"
    Config = OutputGuardConfig

    def run(self, node, config: OutputGuardConfig, ctx: StepContext) -> StepResult:
        values = {}
        for ref in config.input:
            value = ctx.resolve(ref, required=False)
            if value is None:
                raise StepError(f"output_guard '{node.id}': '{ref}' has no output to check")
            values[ref] = value
        result = check_output(
            values,
            canary=step_canary(ctx) if config.canary else None,
            classifier=ctx.ports.extra.get(CLASSIFIER_PORT),
            security=ctx.snapshot.section("security", {}) or {},
        )
        out = result.model_dump()
        out["flag_names"] = result.flag_names()
        if not result.flagged:
            return StepResult(output=out)
        security_flags = []
        for f in result.flags:
            ctx.ports.tracer.security_flag(f.flag, action="quarantine", reason_code=f.reason_code)
            security_flags.append({"flag": f.flag, "detector": f.detector, "reason_code": f.reason_code})
        item = {
            "flagged": True,
            "security_flags": security_flags,
            "security_flag": {"source": "output_guard", "step": node.id, "flags": result.flag_names()},
        }
        return StepResult(output=out, item=item)
