"""``llm`` and ``llm_tools`` steps: one isolated session per execution (FR-002, constitution IV and VIII).

Every execution builds a fresh prompt from: the fixed system prompt (``prompts/system/<system>.md``), the
instruction (``instructions/<instruction>``), at most one context pack (``contexts/<context>.md``) and only
the node's declared ``inputs``. Inputs are wrapped in random delimiters (datamarking). Nothing from earlier
steps or earlier calls leaks in; the model port is called without any message history.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from pkpipe.steps.base import (
    StepConfig,
    StepContext,
    StepError,
    StepExecutor,
    StepResult,
    datamark,
    datamark_notice,
    datamark_token,
    register_step,
)
from pkpipe.steps.tool import call_tool, check_tool_allowed

#: Step types that are one LLM session with an ``LlmConfig`` (every one needs an eval dataset, spec 013);
#: ``reader`` is the secure-fetch Reader (spec 008).
LLM_STEP_TYPES = ("llm", "llm_tools", "reader")


class LlmConfig(StepConfig):
    system: str = Field(description="System prompt name: prompts/system/<system>.md")
    instruction: str | None = Field(default=None, description="Path below instructions/")
    context: str | None = Field(
        default=None, description="Context pack name: contexts/<context>.md (one at most)"
    )
    output_schema: str | None = Field(default=None, description="Schema name: schemas/<name>.json")
    profile: str = Field(default="default", description="Model profile from models.json")


class LlmToolsConfig(LlmConfig):
    tools: list[str] = Field(min_length=1, description="'<server>:<tool>' names the session may call")
    max_turns: int = Field(default=4, ge=1, le=20)


def _asset_refs(config: LlmConfig) -> list[tuple[str, str, bool]]:
    """(path, what, required) for every asset the session reads."""
    refs = [(f"prompts/system/{config.system}.md", "system prompt", True)]
    if config.instruction:
        refs.append((f"instructions/{config.instruction}", "instruction", True))
    if config.output_schema:
        refs.append((f"schemas/{config.output_schema}.json", "output schema", True))
    if config.context:
        refs.append((f"contexts/{config.context}.md", "context pack", False))  # private overlay may lack it
    return refs


def check_session_assets(node_id: str, config: LlmConfig, snapshot) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    for path, what, required in _asset_refs(config):
        if snapshot.asset(path) is None:
            msg = f"node '{node_id}': {what} '{path}' not found"
            (errors if required else warnings).append(msg + ("" if required else " (private overlay?)"))
    if config.output_schema and snapshot.asset(f"schemas/{config.output_schema}.json") is not None:
        try:
            schema = snapshot.json_asset(f"schemas/{config.output_schema}.json")
            if not isinstance(schema, dict):
                errors.append(
                    f"node '{node_id}': output schema '{config.output_schema}' is not a JSON object"
                )
        except ValueError:
            errors.append(f"node '{node_id}': output schema '{config.output_schema}' is not valid JSON")
    return errors, warnings


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1, default=str)


def build_prompt(config: LlmConfig, ctx: StepContext) -> tuple[str, str, dict[str, Any] | None]:
    """(system, user, json_schema) of one session, from declared inputs only."""
    system = ctx.snapshot.asset(f"prompts/system/{config.system}.md")
    if system is None:
        raise StepError(f"system prompt '{config.system}' missing from the pinned config")
    instruction = ctx.snapshot.asset(f"instructions/{config.instruction}") if config.instruction else None
    context = ctx.snapshot.asset(f"contexts/{config.context}.md") if config.context else None
    schema = ctx.snapshot.json_asset(f"schemas/{config.output_schema}.json") if config.output_schema else None
    inputs = {ref: _text(v) for ref, v in ctx.inputs.items() if v is not None}
    token = datamark_token(*inputs.values())
    parts = []
    if instruction:
        parts.append(instruction.strip())
    if context:
        parts.append("## Context\n\n" + context.strip())
    if inputs:
        parts.append(
            "## Inputs\n\n" + "\n\n".join(f"### {ref}\n{datamark(t, token)}" for ref, t in inputs.items())
        )
    return system.strip() + "\n\n" + datamark_notice(token), "\n\n".join(parts), schema


def complete(ctx: StepContext, config: LlmConfig, system: str, user: str, schema: dict[str, Any] | None):
    model = ctx.ports.require("model")
    with ctx.ports.tracer.llm(config.profile) as span:
        span.set("pkpipe.model.profile", config.profile)
        reply = model.complete(system, user, schema, profile=config.profile)
        ctx.ports.tracer.capture(system=system, user=user, response=_text(reply))
    return reply


@register_step
class LlmStep(StepExecutor):
    type = "llm"
    Config = LlmConfig

    def check(self, node, config, snapshot):
        return check_session_assets(node.id, config, snapshot)

    def run(self, node, config: LlmConfig, ctx: StepContext) -> StepResult:
        system, user, schema = build_prompt(config, ctx)
        return StepResult(output=complete(ctx, config, system, user, schema))


DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action"],
    "properties": {
        "action": {"type": "string", "enum": ["call", "final"]},
        "tool": {"type": "string"},
        "arguments": {"type": "object"},
        "answer": {"type": "string"},
    },
}


@register_step
class LlmToolsStep(StepExecutor):
    """A session that may call allowed tools. The model proposes a call; code checks the allow list and
    executes it (constitution V). Tool results are datamarked like any other untrusted data."""

    type = "llm_tools"
    Config = LlmToolsConfig
    uses_tools = True

    def check(self, node, config: LlmToolsConfig, snapshot):
        errors, warnings = check_session_assets(node.id, config, snapshot)
        for tool in config.tools:
            err = check_tool_allowed(tool, snapshot)
            if err:
                errors.append(f"node '{node.id}': {err}")
        return errors, warnings

    def run(self, node, config: LlmToolsConfig, ctx: StepContext) -> StepResult:
        system, user, schema = build_prompt(config, ctx)
        tool_notice = (
            "\n\nYou may call these tools: "
            + ", ".join(config.tools)
            + '. Reply {"action": "call", "tool": ..., "arguments": {...}} to call one, or '
            '{"action": "final", "answer": ...} when you are done.'
        )
        transcript: list[str] = []
        calls = 0
        answer: str | None = None
        for _ in range(config.max_turns):
            prompt = user + ("\n\n## Tool results\n\n" + "\n\n".join(transcript) if transcript else "")
            decision = complete(ctx, config, system + tool_notice, prompt, DECISION_SCHEMA)
            if decision.get("action") != "call":
                answer = decision.get("answer")
                break
            tool = str(decision.get("tool", ""))
            if tool not in config.tools:
                ctx.ports.tracer.security_flag(
                    "tool_allowlist", action="block", reason_code="tool_not_allowed"
                )
                transcript.append(f"Tool '{tool}' is not allowed. Allowed: {', '.join(config.tools)}.")
                continue
            calls += 1
            try:
                result = call_tool(ctx, tool, decision.get("arguments") or {})
                text = _text(result)
            except Exception as exc:  # noqa: BLE001 - the model sees that the call failed, nothing more
                text = f"error: {type(exc).__name__}"
            token = datamark_token(text)
            transcript.append(f"### {tool}\n{datamark(text, token)}\n({datamark_notice(token)})")
        if schema is None:
            return StepResult(output={"text": answer or "", "tool_calls": calls})
        prompt = user + ("\n\n## Tool results\n\n" + "\n\n".join(transcript) if transcript else "")
        final = complete(ctx, config, system, prompt + "\n\nGive the final answer now.", schema)
        return StepResult(output=final)
