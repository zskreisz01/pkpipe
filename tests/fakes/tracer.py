from __future__ import annotations

import contextlib
import contextvars
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

_flow: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("fake_flow", default=None)
_item: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("fake_item", default=None)


@dataclass
class RecordedSpan:
    kind: str  # flow | item | step | llm | tool
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    failed: str | None = None

    def set(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        self.events.append((name, dict(attributes or {})))

    def fail(self, error_type: str) -> None:
        self.failed = error_type


class RecordingTracer:
    """TracerPort fake: records every span with the pkpipe attributes it would carry (thread safe)."""

    def __init__(self, capture: bool = False):
        self.spans: list[RecordedSpan] = []
        self.flags: list[dict[str, Any]] = []
        self.captured: list[dict[str, str]] = []
        self.capture_enabled = capture
        self._lock = threading.Lock()

    def _ctx(self) -> dict[str, Any]:
        flow, item = _flow.get() or {}, _item.get() or {}
        return {
            "flow.id": flow.get("flow_id"),
            "config.version": flow.get("config_version"),
            "run.trigger": flow.get("trigger"),
            "item.id": item.get("item_id"),
            "item.category": item.get("category"),
        }

    @contextlib.contextmanager
    def _record(self, span: RecordedSpan) -> Iterator[RecordedSpan]:
        with self._lock:
            self.spans.append(span)
        try:
            yield span
        except BaseException as exc:
            span.failed = span.failed or type(exc).__name__
            raise

    @contextlib.contextmanager
    def flow(self, flow_id: str, *, trigger: str, config_version: str, data_classes: tuple[str, ...] = ()):
        token = _flow.set({"flow_id": flow_id, "trigger": trigger, "config_version": config_version})
        try:
            with self._record(RecordedSpan("flow", flow_id, self._ctx())) as span:
                yield span
        finally:
            _flow.reset(token)

    @contextlib.contextmanager
    def item(self, item_id: str, category: str | None = None):
        token = _item.set({"item_id": item_id, "category": category})
        try:
            yield RecordedSpan("item", item_id)
        finally:
            _item.reset(token)

    @contextlib.contextmanager
    def step(self, step_id: str, bpmn_element: str, *, item_id: str | None, item_category: str | None):
        attrs = {**self._ctx(), "step.id": step_id, "bpmn.element": bpmn_element}
        if item_id is not None:
            attrs["item.id"] = item_id
        if item_category is not None:
            attrs["item.category"] = item_category
        with self._record(RecordedSpan("step", step_id, attrs)) as span:
            yield span

    @contextlib.contextmanager
    def llm(self, model: str):
        with self._record(RecordedSpan("llm", model, self._ctx())) as span:
            yield span

    @contextlib.contextmanager
    def tool(self, tool: str):
        with self._record(RecordedSpan("tool", tool, self._ctx())) as span:
            yield span

    def capture(self, **content: str) -> bool:
        if self.capture_enabled:
            self.captured.append(dict(content))
        return self.capture_enabled

    def security_flag(self, detector: str, *, action: str = "flag", reason_code: str | None = None) -> None:
        with self._lock:
            self.flags.append({"detector": detector, "action": action, "reason_code": reason_code})

    def of(self, kind: str, name: str | None = None) -> list[RecordedSpan]:
        return [s for s in self.spans if s.kind == kind and (name is None or s.name == name)]
