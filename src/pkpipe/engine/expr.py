"""References, templates and conditions over flow state (spec 005 T003).

A *reference* is a dotted path whose first segment is a root:

* ``item``   – the item of a per-item run (fan-out element or subflow item)
* ``input``  – the inputs the run was started with
* ``run``    – ``run_id``, ``flow_id``, ``trigger``, ``config_version``
* ``error``  – the last handled step failure (``node``, ``type``, ``message``)
* ``config`` – the pinned effective config (read only), e.g. ``config.categories``
* ``result`` – the value a step is currently checking (wait ``until``)
* ``<node id>`` – the output of that step (for a ``for_each`` step: the list of per-item results)

Later segments index dicts by key and lists by position. Templates replace ``${ref}``; a string that is
exactly one ``${ref}`` yields the referenced value itself (a list stays a list).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from pkpipe.engine.flow_models import Condition


class ReferenceError_(LookupError):
    """A reference names nothing in the current state."""


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING: Any = _Missing()
_TEMPLATE = re.compile(r"\$\{([^}]+)\}")
_WHOLE = re.compile(r"^\$\{([^}]+)\}$")

Resolver = Callable[[str], Any]


def lookup(root: Mapping[str, Any], ref: str) -> Any:
    """Walk ``ref`` through ``root``; :data:`MISSING` when any segment is absent."""
    current: Any = root
    for part in ref.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return MISSING
            current = current[part]
        elif isinstance(current, list | tuple) and part.lstrip("-").isdigit():
            idx = int(part)
            if not -len(current) <= idx < len(current):
                return MISSING
            current = current[idx]
        else:
            return MISSING
    return current


def resolve(resolver: Resolver, ref: str, *, required: bool = True) -> Any:
    value = resolver(ref.strip())
    if value is MISSING and required:
        raise ReferenceError_(f"reference '{ref}' is not available in this state")
    return value


def render(value: Any, resolver: Resolver) -> Any:
    """Fill ``${ref}`` placeholders in strings, recursively through dicts and lists."""
    if isinstance(value, str):
        whole = _WHOLE.match(value)
        if whole:
            return resolve(resolver, whole.group(1))

        def sub(m: re.Match[str]) -> str:
            v = resolve(resolver, m.group(1))
            return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)

        return _TEMPLATE.sub(sub, value)
    if isinstance(value, dict):
        return {k: render(v, resolver) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, resolver) for v in value]
    return value


def references_in(value: Any) -> list[str]:
    """Every ``${ref}`` used in a template value (for validation)."""
    if isinstance(value, str):
        return [m.strip() for m in _TEMPLATE.findall(value)]
    if isinstance(value, dict):
        return [r for v in value.values() for r in references_in(v)]
    if isinstance(value, list):
        return [r for v in value for r in references_in(v)]
    return []


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def evaluate(cond: Condition, resolver: Resolver) -> bool:
    """Deterministic evaluation; a missing path only satisfies ``exists: false``, ``ne`` and ``not_in``."""
    if cond.all is not None:
        return all(evaluate(c, resolver) for c in cond.all)
    if cond.any is not None:
        return any(evaluate(c, resolver) for c in cond.any)
    if cond.not_ is not None:
        return not evaluate(cond.not_, resolver)
    assert cond.path is not None
    value = resolver(cond.path)
    s = cond.model_fields_set
    if "exists" in s:
        return (value is not MISSING) == bool(cond.exists)
    if "truthy" in s:
        return (value is not MISSING and bool(value)) == bool(cond.truthy)
    if "eq" in s:
        return value is not MISSING and value == cond.eq
    if "ne" in s:
        return value is MISSING or value != cond.ne
    if "in_" in s:
        return value is not MISSING and value in (cond.in_ or [])
    if "not_in" in s:
        return value is MISSING or value not in (cond.not_in or [])
    n = _num(value)
    if n is None:
        return False
    if "gt" in s:
        return n > float(cond.gt)  # type: ignore[arg-type]
    if "gte" in s:
        return n >= float(cond.gte)  # type: ignore[arg-type]
    if "lt" in s:
        return n < float(cond.lt)  # type: ignore[arg-type]
    return n <= float(cond.lte)  # type: ignore[arg-type]


def condition_paths(cond: Condition) -> list[str]:
    out = [cond.path] if cond.path else []
    for sub in (cond.all or []) + (cond.any or []) + ([cond.not_] if cond.not_ else []):
        out.extend(condition_paths(sub))
    return out
