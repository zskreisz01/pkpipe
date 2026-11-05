from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from pkpipe.ports import ImageInput, ImagesNotSupported, ModelError


class FakeModel:
    """ModelPort fake: scripted replies and a record of every call.

    Script items (queue order, or one callable ``(system, user) -> dict``) may be a dict (returned) or an
    exception instance (raised, e.g. ``SchemaError("x")``). Pass ``schema_check=True`` to validate dict
    replies against the requested schema like the real adapter does.

    Images (spec 021): ``calls`` records the sha256 digests under ``images`` (never the bytes). A call with
    images to a profile outside ``vision_profiles`` raises :class:`ImagesNotSupported` like the adapter does
    (``vision_profiles=None``: every profile may take images).
    """

    def __init__(
        self,
        replies: list[dict[str, Any] | Exception] | Callable[..., dict[str, Any]] | None = None,
        schema_check: bool = False,
        vision_profiles: set[str] | None = None,
    ):
        self._replies = replies if replies is not None else []
        self.schema_check = schema_check
        self.vision_profiles = vision_profiles
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None = None,
        profile: str = "default",
        images: Sequence[ImageInput] | None = None,
    ) -> dict[str, Any]:
        digests = [i.sha256 for i in images or ()]
        self.calls.append(
            {
                "system": system,
                "user": user,
                "json_schema": json_schema,
                "profile": profile,
                "images": digests,
            }
        )
        if digests and self.vision_profiles is not None and profile not in self.vision_profiles:
            raise ImagesNotSupported(f"profile '{profile}' does not declare vision: true")
        if callable(self._replies):
            # a callable that sets ``wants_schema`` also receives the requested JSON schema; one that sets
            # ``wants_images`` also receives the digests of the attached images (keyword ``images``)
            args: list[Any] = [system, user]
            kwargs: dict[str, Any] = {}
            if getattr(self._replies, "wants_schema", False):
                args.append(json_schema)
            if getattr(self._replies, "wants_images", False):
                kwargs["images"] = digests
            reply = self._replies(*args, **kwargs)
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
