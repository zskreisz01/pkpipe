"""``code``, ``notify``, ``subflow``, ``wait`` and ``guard`` steps (spec 005 FR-001)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field, field_validator

from pkpipe.engine.expr import MISSING, evaluate, lookup
from pkpipe.engine.flow_models import Condition
from pkpipe.steps.base import (
    CODE,
    StepConfig,
    StepContext,
    StepError,
    StepExecutor,
    StepResult,
    register_step,
)
from pkpipe.steps.tool import call_tool, check_tool_allowed


# -- code ---------------------------------------------------------------------------------------------------
class CodeConfig(StepConfig):
    function: str = Field(description="Name registered with pkpipe.steps.register_code")
    args: dict[str, Any] = Field(
        default_factory=dict, description="Keyword arguments; ${ref} templates allowed"
    )


@register_step
class CodeStep(StepExecutor):
    """Deterministic Python: gates, state changes, row building (constitution V)."""

    type = "code"
    Config = CodeConfig

    def check(self, node, config: CodeConfig, snapshot):
        if config.function not in CODE:
            return [f"node '{node.id}': unknown code function '{config.function}'"], []
        return [], []

    def run(self, node, config: CodeConfig, ctx: StepContext) -> StepResult:
        out = CODE[config.function](ctx, **ctx.render(config.args))
        return out if isinstance(out, StepResult) else StepResult(output=out)


# -- notify -------------------------------------------------------------------------------------------------
class NotifyConfig(StepConfig):
    event: str = Field(description="Event type; channels come from channels.json routes[event]")
    message: str = Field(
        default="", description="Message template with ${ref} placeholders (payload.message)"
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra template variables for the event template; ${ref} templates allowed",
    )
    channels: list[str] | None = Field(default=None, description="Explicit channels instead of the route")


@register_step
class NotifyStep(StepExecutor):
    """Hands an event to the NotifyPort (spec 006). Delivery failures are reported in the output and traced
    by the adapter; they never fail the flow."""

    type = "notify"
    Config = NotifyConfig

    def check(self, node, config: NotifyConfig, snapshot):
        known = (snapshot.data.get("channels") or {}).get("channels") or {}
        missing = [c for c in config.channels or [] if c not in known]
        return ([f"node '{node.id}': unknown channel(s) {missing}"] if missing else []), []

    def run(self, node, config: NotifyConfig, ctx: StepContext) -> StepResult:
        payload = ctx.render(config.payload)
        if config.message:
            payload["message"] = ctx.render(config.message)
        deliveries = ctx.ports.require("notify").send(config.event, payload, config.channels)
        if not deliveries:
            return StepResult(output={"event": config.event, "sent": [], "skipped": "no route"})
        out: dict[str, Any] = {"event": config.event, "sent": [d.channel for d in deliveries if d.ok]}
        failed = [{"channel": d.channel, "error": d.error} for d in deliveries if not d.ok]
        if failed:
            out["failed"] = failed
        return StepResult(output=out)


# -- subflow ------------------------------------------------------------------------------------------------
class SubflowConfig(StepConfig):
    flow: str = Field(description="Flow id, or a ${ref} template such as ${item.subflow}")


@register_step
class SubflowStep(StepExecutor):
    """Runs another flow for the current item. Its checkpoints nest under this run, so it resumes too."""

    type = "subflow"
    Config = SubflowConfig

    def check(self, node, config: SubflowConfig, snapshot):
        if "${" in config.flow:
            return [], []
        if config.flow not in (snapshot.data.get("flows") or {}):
            return [f"node '{node.id}': subflow '{config.flow}' does not exist"], []
        return [], []

    def run(self, node, config: SubflowConfig, ctx: StepContext) -> StepResult:
        flow_id = ctx.render(config.flow)
        if not isinstance(flow_id, str) or flow_id not in (ctx.snapshot.data.get("flows") or {}):
            raise StepError(f"subflow '{flow_id}' does not exist")
        if ctx.run_subflow is None:
            raise StepError("subflows are not available in this context")
        final = ctx.run_subflow(flow_id, ctx.item)
        item = final.get("item")
        return StepResult(
            output={"flow": flow_id, "outputs": final.get("outputs", {}), "item": item},
            item=item if isinstance(item, dict) else None,
        )


# -- wait ---------------------------------------------------------------------------------------------------
class WaitDeadline(StepError):
    """The wait reached its deadline without the ``until`` condition becoming true."""


class WaitConfig(StepConfig):
    tool: str = Field(description="Status tool polled on every attempt")
    arguments: dict[str, Any] = Field(default_factory=dict)
    until: Condition = Field(description="Condition on 'result' (the tool result) that ends the wait")
    poll_s: float = Field(default=60.0, gt=0, description="Pause before each poll")
    deadline_s: float | None = Field(
        default=None, gt=0, description="From the first attempt; None = no limit"
    )


@register_step
class WaitStep(StepExecutor):
    """Poll + deadline (docs/process/wait-for-generation-result). Each attempt is one checkpointed step, so a
    restart resumes the wait with the original deadline instead of starting over (SC-002)."""

    type = "wait"
    Config = WaitConfig
    loops = True

    def check(self, node, config: WaitConfig, snapshot):
        err = check_tool_allowed(config.tool, snapshot)
        return ([f"node '{node.id}': {err}"] if err else []), []

    def run(self, node, config: WaitConfig, ctx: StepContext) -> StepResult:
        clock = ctx.ports.clock
        state = dict(ctx.wait or {})
        if not state:
            state = {"started": clock.now(), "polls": 0}
            if config.deadline_s is not None:
                state["deadline"] = state["started"] + config.deadline_s
        deadline = state.get("deadline")
        ctx.set_status("waiting")
        pause = config.poll_s
        if deadline is not None:
            pause = max(0.0, min(pause, deadline - clock.now()))
        clock.sleep(pause)
        result = call_tool(ctx, config.tool, ctx.render(config.arguments))
        state["polls"] = int(state.get("polls", 0)) + 1

        def resolver(ref: str) -> Any:
            root, _, rest = ref.partition(".")
            if root != "result":
                return ctx.resolver(ref)
            return result if not rest else lookup(result if isinstance(result, dict) else {}, rest)

        if evaluate(config.until, resolver):
            ctx.set_status("running")
            return StepResult(output=result)
        if deadline is not None and clock.now() >= deadline:
            ctx.set_status("running")
            raise WaitDeadline(f"wait '{node.id}' passed its deadline after {state['polls']} poll(s)")
        return StepResult(pending=state)


# -- guard --------------------------------------------------------------------------------------------------
class GuardCheck(StepConfig):
    detector: str
    tool: str = Field(description="Scoring tool, e.g. a prompt-guard classifier behind MCP")
    arguments: dict[str, Any] = Field(default_factory=dict)
    score: str = Field(default="score", description="Path of the score in the tool result")
    threshold: float = Field(ge=0.0, le=1.0)


class GuardConfig(StepConfig):
    input: str = Field(description="Reference to the text (or object) to check")
    patterns: list[str] = Field(default_factory=list, description="Regular expressions that raise a flag")
    checks: list[GuardCheck] = Field(default_factory=list)

    @field_validator("patterns")
    @classmethod
    def _compile(cls, v: list[str]) -> list[str]:
        for p in v:
            try:
                re.compile(p)
            except re.error as exc:
                raise ValueError(f"invalid pattern {p!r}: {exc}") from exc
        return v


def pattern_flags(patterns: list[str], text: str) -> list[dict[str, Any]]:
    """Flags of the pattern detector: one per matching pattern (case-insensitive). Also used by the security
    eval (spec 013), so the eval measures exactly what the guard step does."""
    return [
        {"detector": "pattern", "reason_code": f"pattern_{i}"}
        for i, pattern in enumerate(patterns)
        if re.search(pattern, text, re.IGNORECASE)
    ]


@register_step
class GuardStep(StepExecutor):
    """Code raises the flag from patterns and classifier scores (constitution V); the flow routes on
    ``item.flagged``. Only detector and reason code are recorded, never the text."""

    type = "guard"
    Config = GuardConfig

    def check(self, node, config: GuardConfig, snapshot):
        errors = []
        for c in config.checks:
            err = check_tool_allowed(c.tool, snapshot)
            if err:
                errors.append(f"node '{node.id}': {err}")
        return errors, []

    def run(self, node, config: GuardConfig, ctx: StepContext) -> StepResult:
        value = ctx.resolve(config.input, required=False)
        text = value if isinstance(value, str) else ("" if value is None else repr(value))
        flags = pattern_flags(config.patterns, text)
        for c in config.checks:
            result = call_tool(ctx, c.tool, ctx.render(c.arguments))
            score = lookup(result, c.score) if isinstance(result, dict) else result
            if score is MISSING or not isinstance(score, int | float):
                flags.append({"detector": c.detector, "reason_code": "no_score"})  # fail closed
            elif score >= c.threshold:
                flags.append({"detector": c.detector, "reason_code": "threshold"})
        for f in flags:
            ctx.ports.tracer.security_flag(f["detector"], action="flag", reason_code=f["reason_code"])
        output = {"flagged": bool(flags), "flags": flags}
        return StepResult(output=output, item={"flagged": True, "security_flags": flags} if flags else None)
