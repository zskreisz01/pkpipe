"""``env:NAME|alt`` references of config values, resolved without importing an adapter (spec 021 needs the
model id of a profile in the core: the description cache is keyed by it)."""

from __future__ import annotations

from collections.abc import Mapping


def resolve_chain(value: str, environ: Mapping[str, str]) -> str | None:
    """What ``env:NAME|alt|...`` resolves to in ``environ``: the first set ``env:`` variable, else the first
    literal; ``None`` when nothing resolves. A value without ``env:`` is returned as it is."""
    if not value.startswith("env:"):
        return value
    for part in value.split("|"):
        if not part.startswith("env:"):
            return part
        if environ.get(part[4:]):
            return environ[part[4:]]
    return None
