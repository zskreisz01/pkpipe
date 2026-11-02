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
SPAN_KIND = "pkpipe.span.kind"  # flow | step | llm | tool | notify | eval | system

# Evaluation runs (spec 013 FR-004): every eval span carries the run id, suite and case.
EVAL_RUN_ID = "eval.run_id"
EVAL_SUITE = "eval.suite"
EVAL_LEVEL = "eval.level"
EVAL_CASE_ID = "eval.case_id"

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
    "reranker.",  # query and input/output documents
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

# System preflight and sampler (docs/operations.md "System preflight"): gauges of the latest sample.
M_SYSTEM_MEMORY_PRESSURE = "pkpipe.system.memory_pressure"  # 0 normal, 1 warn, 2 critical, -1 unknown
M_SYSTEM_MEMORY_AVAILABLE = "pkpipe.system.memory_available_ratio"
M_SYSTEM_SWAP_USED = "pkpipe.system.swap_used_bytes"
M_SYSTEM_THERMAL = "pkpipe.system.thermal_level"  # 0 nominal .. 3 critical, -1 unknown
M_SYSTEM_LOAD = "pkpipe.system.load_per_cpu"
M_SYSTEM_DECISION = "pkpipe.system.decision"  # 0 ok, 1 degraded, 2 blocked
M_SYSTEM_CONCURRENCY = "pkpipe.system.eval_concurrency"
SYSTEM_PREFLIGHT_SPAN = "system.preflight"
