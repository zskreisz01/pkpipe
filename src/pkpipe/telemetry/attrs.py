"""Span attribute names and metric names used across pkpipe (docs/operations.md)."""

from __future__ import annotations

FLOW_ID = "flow.id"
STEP_ID = "step.id"
BPMN_ELEMENT = "bpmn.element"
ITEM_ID = "item.id"
ITEM_CATEGORY = "item.category"
CONFIG_VERSION = "config.version"
RUN_TRIGGER = "run.trigger"
CONTENT_CAPTURE = "pkpipe.content_capture"
SPAN_KIND = "pkpipe.span.kind"  # flow | step | llm | tool

REQUIRED_STEP_ATTRIBUTES = (
    FLOW_ID,
    STEP_ID,
    BPMN_ELEMENT,
    ITEM_ID,
    ITEM_CATEGORY,
    CONFIG_VERSION,
    RUN_TRIGGER,
)

SECURITY_FLAG_EVENT = "security.flag"

# Attribute key prefixes that can carry prompt/response/tool payloads. Stripped before export when a span
# runs under a flow whose content capture is off (journal and context data, or the default).
CONTENT_PREFIXES = (
    "input.",
    "output.",
    "llm.input_messages",
    "llm.output_messages",
    "llm.prompts",
    "llm.prompt_template",
    "llm.invocation_parameters",  # can embed prompts/stop strings with some providers
    "llm.choices",
    "retrieval.documents",
    "embedding.",
    "tool.parameters",
    "tool_call.function.arguments",
    "metadata",
    "pkpipe.content.",
)

# Metric names
M_ITEMS = "pkpipe.items_total"
M_BACKLOG = "pkpipe.backlog_size"
M_FLAGS = "pkpipe.flags_total"
M_LEARNING_TIMEOUTS = "pkpipe.learning_timeouts_total"
M_LEARNING_SECONDS = "pkpipe.learning_generation_seconds"
M_STEP_ERRORS = "pkpipe.step_errors_total"
M_TOKENS_PER_SECOND = "pkpipe.tokens_per_second"
M_EXPORT_FAILURES = "pkpipe.telemetry_export_failures_total"
