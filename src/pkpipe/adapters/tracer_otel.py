"""TracerPort on ``pkpipe.telemetry`` (spec 003): flow/step/LLM/tool spans with the pkpipe attributes."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from pkpipe import telemetry


class OtelSpan:
    def __init__(self, span: Span, step_id: str | None = None):
        self.span = span
        self.step_id = step_id

    def set(self, key: str, value: Any) -> None:
        self.span.set_attribute(key, value)

    def event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        self.span.add_event(name, attributes or {})

    def fail(self, error_type: str) -> None:
        """A failure the flow handles (error edge, on_item_error): the span still shows the step failed."""
        self.span.set_status(Status(StatusCode.ERROR))
        self.span.add_event("exception", {"exception.type": error_type})
        if self.step_id:
            telemetry.get_telemetry().metrics.step_error(self.step_id, error_type)


class OtelTracer:
    """Implements :class:`pkpipe.ports.TracerPort`."""

    @contextlib.contextmanager
    def flow(
        self, flow_id: str, *, trigger: str, config_version: str, data_classes: tuple[str, ...] = ()
    ) -> Iterator[OtelSpan]:
        with telemetry.flow_span(
            flow_id, trigger=trigger, config_version=config_version, data_classes=data_classes
        ):
            yield OtelSpan(trace.get_current_span())

    @contextlib.contextmanager
    def item(self, item_id: str, category: str | None = None) -> Iterator[OtelSpan]:
        with telemetry.item_context(item_id, category):
            yield OtelSpan(trace.get_current_span())

    @contextlib.contextmanager
    def step(
        self, step_id: str, bpmn_element: str, *, item_id: str | None, item_category: str | None
    ) -> Iterator[OtelSpan]:
        with telemetry.step_span(step_id, bpmn_element, item_id=item_id, item_category=item_category) as span:
            yield OtelSpan(span, step_id)

    @contextlib.contextmanager
    def llm(self, model: str) -> Iterator[OtelSpan]:
        with telemetry.llm_span(model) as span:
            yield OtelSpan(span)

    @contextlib.contextmanager
    def tool(self, tool: str) -> Iterator[OtelSpan]:
        with telemetry.tool_span(tool) as span:
            yield OtelSpan(span)

    def capture(self, **content: str) -> bool:
        return telemetry.capture_content(**content)

    def security_flag(self, detector: str, *, action: str = "flag", reason_code: str | None = None) -> None:
        telemetry.record_security_flag(detector, action=action, reason_code=reason_code)
