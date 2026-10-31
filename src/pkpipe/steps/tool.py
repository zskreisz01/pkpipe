"""``tool`` step: one deterministic tool call through the ToolPort (no model involved)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from pkpipe.steps.base import StepConfig, StepContext, StepExecutor, StepResult, register_step


class ToolConfig(StepConfig):
    tool: str = Field(description="'<server>:<tool>' from mcp_servers.json, on that server's allow list")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Values may use ${ref} templates")


def check_tool_allowed(tool: str, snapshot) -> str | None:
    """Why ``tool`` may not be called under this config, or None."""
    server, sep, name = tool.partition(":")
    if not sep or not server or not name:
        return f"tool '{tool}' must be '<server>:<tool>'"
    servers = snapshot.data.get("mcp_servers") or {}
    if server not in servers:
        return f"tool '{tool}' references unregistered MCP server '{server}'"
    if name not in (servers[server].get("allow") or []):
        return f"tool '{tool}' is not in the allow list of '{server}'"
    return None


def call_tool(ctx: StepContext, tool: str, arguments: dict[str, Any]) -> Any:
    """Allow-list check (again, at run time) and the call inside a ``tool`` span."""
    err = check_tool_allowed(tool, ctx.snapshot)
    if err:
        raise PermissionError(err)
    tools = ctx.ports.require("tools")
    with ctx.ports.tracer.tool(tool):
        return tools.call(tool, arguments)


@register_step
class ToolStep(StepExecutor):
    type = "tool"
    Config = ToolConfig

    def check(self, node, config: ToolConfig, snapshot):
        err = check_tool_allowed(config.tool, snapshot)
        errors = [f"node '{node.id}': {err}"] if err else []
        return errors, []

    def run(self, node, config: ToolConfig, ctx: StepContext) -> StepResult:
        return StepResult(output=call_tool(ctx, config.tool, ctx.render(config.arguments)))
