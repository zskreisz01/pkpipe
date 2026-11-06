"""Data classes of a flow (``Flow.data_classes``): what a flow handles decides where it may run.

``journal`` (spec 015), ``context`` (spec 011) and ``image`` (spec 021) are private: the flow uses loopback
model profiles only, calls no tool of an ``egress`` server, starts only subflows of the same classes, and no
prompt or answer of its sessions is ever offered to a trace backend, whatever the telemetry config lists.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator

PRIVATE_DATA_CLASSES = frozenset({"journal", "context", "image"})


def is_private(data_classes: object) -> bool:
    """Whether a flow with these data classes handles private data."""
    return bool(PRIVATE_DATA_CLASSES & set(data_classes or ()))  # type: ignore[call-overload]


_content_free: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "pkpipe_content_free_errors", default=False
)


@contextlib.contextmanager
def content_free_errors(active: bool) -> Iterator[None]:
    """While active, code that records an error (a model span's status and events, a step failure) keeps the
    error type and drops its message: a schema violation or a server error can quote text of the private
    content the call was about (spec 021 safety review). The flag follows the call into worker threads that
    run in a copy of the context."""
    token = _content_free.set(bool(active))
    try:
        yield
    finally:
        _content_free.reset(token)


def errors_are_content_free() -> bool:
    return _content_free.get()
