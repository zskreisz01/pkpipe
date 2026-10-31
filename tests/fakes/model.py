from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pkpipe.ports import ModelError


class FakeModel:
    """ModelPort fake: scripted replies and a record of every call.

    Script items (queue order, or one callable ``(system, user) -> dict``) may be a dict (returned) or an
    exception instance (raised, e.g. ``SchemaError("x")``). Pass ``schema_check=True`` to validate dict
    replies against the requested schema like the real adapter does.
    """

    def __init__(
        self,
        replies: list[dict[str, Any] | Exception] | Callable[[str, str], dict[str, Any]] | None = None,
        schema_check: bool = False,
    ):
        self._replies = replies if replies is not None else []
        self.schema_check = schema_check
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None = None,
        profile: str = "default",
    ) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user, "json_schema": json_schema, "profile": profile})
        if callable(self._replies):
            reply = self._replies(system, user)
        elif not self._replies:
            raise AssertionError("FakeModel has no scripted reply left")
        else:
            reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if self.schema_check and json_schema is not None:
            import jsonschema

            try:
                jsonschema.validate(reply, json_schema)
            except jsonschema.ValidationError as exc:
                from pkpipe.ports import SchemaError

                raise SchemaError(f"fake reply violates schema: {exc.message}") from exc
        return reply


__all__ = ["FakeModel", "ModelError"]
