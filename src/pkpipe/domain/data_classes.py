"""Data classes of a flow (``Flow.data_classes``): what a flow handles decides where it may run.

``journal`` (spec 015), ``context`` (spec 011) and ``image`` (spec 021) are private: the flow uses loopback
model profiles only, calls no tool of an ``egress`` server, starts only subflows of the same classes, and no
prompt or answer of its sessions is ever offered to a trace backend, whatever the telemetry config lists.
"""

from __future__ import annotations

PRIVATE_DATA_CLASSES = frozenset({"journal", "context", "image"})


def is_private(data_classes: object) -> bool:
    """Whether a flow with these data classes handles private data."""
    return bool(PRIVATE_DATA_CLASSES & set(data_classes or ()))  # type: ignore[call-overload]
