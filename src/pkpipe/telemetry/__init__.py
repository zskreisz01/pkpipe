"""OpenTelemetry tracing and metrics (spec 003): OTLP only, pkpipe attributes, content capture gated per flow.

Other specs use: ``flow_span``, ``item_context``, ``step_span`` / ``traced_step``, ``llm_span``,
``tool_span``,
``set_span_attributes``, ``set_item_category``, ``capture_content``, ``record_security_flag`` and
``get_telemetry().metrics``. Bootstrap once with ``configure()``; see docs/operations.md.
"""

from .attrs import REQUIRED_STEP_ATTRIBUTES
from .config import PRIVATE_DATA_CLASSES, ContentPolicy, TelemetryConfig
from .runtime import Metrics, Telemetry, configure, get_telemetry, reset
from .spans import (
    RunContext,
    capture_content,
    current_run,
    flow_span,
    item_context,
    llm_span,
    record_security_flag,
    set_item_category,
    set_span_attributes,
    step_span,
    tool_span,
    traced_step,
)

__all__ = [
    "PRIVATE_DATA_CLASSES",
    "REQUIRED_STEP_ATTRIBUTES",
    "ContentPolicy",
    "Metrics",
    "RunContext",
    "Telemetry",
    "TelemetryConfig",
    "capture_content",
    "configure",
    "current_run",
    "flow_span",
    "get_telemetry",
    "item_context",
    "llm_span",
    "record_security_flag",
    "reset",
    "set_item_category",
    "set_span_attributes",
    "step_span",
    "tool_span",
    "traced_step",
]
