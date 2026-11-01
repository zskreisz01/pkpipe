"""Flow/step/LLM/tool spans with pkpipe attributes (FR-003), content gating (FR-004), flags (FR-005)."""

from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from . import attrs
from .runtime import CAPTURE_KEY, get_telemetry


@dataclass
class RunContext:
    flow_id: str
    trigger: str
    config_version: str
    capture: bool


@dataclass
class ItemContext:
    item_id: str
    category: str | None = None


_run: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar("pkpipe_run", default=None)
_item: contextvars.ContextVar[ItemContext | None] = contextvars.ContextVar("pkpipe_item", default=None)


@dataclass
class StepContext:
    step_id: str
    bpmn_element: str
    item_id: str
    item_category: str


#: The step being executed: LLM and tool spans below it inherit its attributes (constitution X).
_step: contextvars.ContextVar[StepContext | None] = contextvars.ContextVar("pkpipe_step", default=None)


def current_run() -> RunContext | None:
    return _run.get()


def set_span_attributes(
    span: Span | None = None,
    *,
    flow_id: str | None = None,
    step_id: str | None = None,
    bpmn_element: str | None = None,
    item_id: str | None = None,
    item_category: str | None = None,
    config_version: str | None = None,
    run_trigger: str | None = None,
) -> None:
    """Set the standard pkpipe attributes on ``span`` (default: current span); ``None`` is skipped."""
    span = span or trace.get_current_span()
    pairs = {
        attrs.FLOW_ID: flow_id,
        attrs.STEP_ID: step_id,
        attrs.BPMN_ELEMENT: bpmn_element,
        attrs.ITEM_ID: item_id,
        attrs.ITEM_CATEGORY: item_category,
        attrs.CONFIG_VERSION: config_version,
        attrs.RUN_TRIGGER: run_trigger,
    }
    for key, value in pairs.items():
        if value is not None:
            span.set_attribute(key, value)


@contextlib.contextmanager
def flow_span(
    flow_id: str,
    *,
    trigger: str,
    config_version: str,
    data_classes: tuple[str, ...] = (),
) -> Iterator[RunContext]:
    """Root span of a flow run. Decides content capture for everything below it (FR-004)."""
    tel = get_telemetry()
    capture = tel.policy.capture_for(flow_id, data_classes)
    run = RunContext(flow_id, trigger, config_version, capture)
    token = _run.set(run)
    ctx_token = otel_context.attach(otel_context.set_value(CAPTURE_KEY, capture))
    try:
        with tel.tracer.start_as_current_span(
            f"flow {flow_id}", record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute(attrs.SPAN_KIND, "flow")
            set_span_attributes(span, flow_id=flow_id, config_version=config_version, run_trigger=trigger)
            try:
                yield run
            except BaseException as exc:
                _mark_error(span, exc)
                raise
    finally:
        otel_context.detach(ctx_token)
        _run.reset(token)


@contextlib.contextmanager
def item_context(item_id: str, category: str | None = None) -> Iterator[ItemContext]:
    item = ItemContext(item_id, category)
    token = _item.set(item)
    try:
        yield item
    finally:
        _item.reset(token)


def set_item_category(category: str) -> None:
    """Record the category once triage knows it: current item context and current span."""
    item = _item.get()
    if item is not None:
        item.category = category
    step = _step.get()
    if step is not None:
        step.item_category = category
    trace.get_current_span().set_attribute(attrs.ITEM_CATEGORY, category)


def _mark_error(span: Span, exc: BaseException) -> None:
    """Error status and event with the exception *type* only; messages may contain private text."""
    span.set_status(Status(StatusCode.ERROR))
    span.add_event("exception", {"exception.type": type(exc).__qualname__})


@contextlib.contextmanager
def step_span(
    step_id: str,
    bpmn_element: str,
    *,
    item_id: str | None = None,
    item_category: str | None = None,
) -> Iterator[Span]:
    """One span per executed step with all required attributes (SC-001).

    Every one of the seven attributes is always present so a backend can filter on them; a value that does not
    apply (a run-level step without an item, an item not yet triaged) is the empty string.
    """
    tel = get_telemetry()
    run, item = _run.get(), _item.get()
    with tel.tracer.start_as_current_span(
        f"step {step_id}", record_exception=False, set_status_on_exception=False
    ) as span:
        span.set_attribute(attrs.SPAN_KIND, "step")
        step = StepContext(
            step_id,
            bpmn_element,
            item_id or (item.item_id if item else "") or "",
            item_category or (item.category if item else "") or "",
        )
        set_span_attributes(
            span,
            flow_id=run.flow_id if run else "",
            step_id=step_id,
            bpmn_element=bpmn_element,
            item_id=step.item_id,
            item_category=step.item_category,
            config_version=run.config_version if run else "",
            run_trigger=run.trigger if run else "",
        )
        token = _step.set(step)
        try:
            yield span
        except BaseException as exc:
            _mark_error(span, exc)
            tel.metrics.step_error(step_id, type(exc).__qualname__)
            raise
        finally:
            _step.reset(token)


def traced_step(step_id: str, bpmn_element: str) -> Callable:
    """Decorator form of ``step_span`` for sync and async step functions.

    Item id/category come from ``item_context`` or the ``item_id`` / ``item_category`` keyword arguments.
    """

    def deco(fn: Callable) -> Callable:
        def _item_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
            return {k: kwargs.get(k) for k in ("item_id", "item_category") if isinstance(kwargs.get(k), str)}

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args, **kwargs):
                with step_span(step_id, bpmn_element, **_item_kwargs(kwargs)):
                    return await fn(*args, **kwargs)

            return awrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with step_span(step_id, bpmn_element, **_item_kwargs(kwargs)):
                return fn(*args, **kwargs)

        return wrapper

    return deco


@contextlib.contextmanager
def llm_span(model: str) -> Iterator[Span]:
    with _child("llm", f"llm {model}", {"llm.model_name": model}) as span:
        yield span


@contextlib.contextmanager
def tool_span(tool: str) -> Iterator[Span]:
    with _child("tool", f"tool {tool}", {"tool.name": tool}) as span:
        yield span


@contextlib.contextmanager
def notify_span(event: str, channel: str, channel_type: str) -> Iterator[Span]:
    """``notify.send``: one delivery of an event to one channel (spec 006), with the step attributes."""
    extra = {"notify.event": event, "notify.channel": channel, "notify.channel_type": channel_type}
    with _child("notify", "notify.send", extra) as span:
        yield span


@contextlib.contextmanager
def _child(kind: str, name: str, extra: dict[str, str]) -> Iterator[Span]:
    tel = get_telemetry()
    run, item = _run.get(), _item.get()
    with tel.tracer.start_as_current_span(
        name, kind=SpanKind.CLIENT, record_exception=False, set_status_on_exception=False
    ) as span:
        span.set_attribute(attrs.SPAN_KIND, kind)
        for k, v in extra.items():
            span.set_attribute(k, v)
        step = _step.get()
        set_span_attributes(
            span,
            flow_id=run.flow_id if run else None,
            step_id=step.step_id if step else None,
            bpmn_element=step.bpmn_element if step else None,
            item_id=(step.item_id if step else None) or (item.item_id if item else None),
            item_category=(step.item_category if step else None) or (item.category if item else None),
            config_version=run.config_version if run else None,
        )
        try:
            yield span
        except BaseException as exc:
            _mark_error(span, exc)
            raise


def capture_content(span: Span | None = None, **content: str) -> bool:
    """Attach prompt/response text as ``pkpipe.content.<name>``, only if the running flow allows it.

    Returns whether anything was attached. The scrubbing exporter is a second line of defence.
    """
    run = _run.get()
    if run is None or not run.capture:
        return False
    span = span or trace.get_current_span()
    for name, text in content.items():
        span.set_attribute(f"pkpipe.content.{name}", text)
    return True


def record_security_flag(detector: str, *, action: str = "flag", reason_code: str | None = None) -> None:
    """FR-005: span event ``security.flag`` on the current span, plus ``pkpipe.flags_total{detector}``.

    Only detector, action and a reason *code* are recorded; never the offending text.
    """
    attributes = {"detector": detector, "action": action}
    if reason_code:
        attributes["reason_code"] = reason_code
    trace.get_current_span().add_event(attrs.SECURITY_FLAG_EVENT, attributes)
    get_telemetry().metrics.flag(detector)
