"""A fake vision model for spec 021: replies scripted by the digest of the prepared image.

``FakeVisionModel(script)`` answers a call with images from ``script`` (a key is the full sha256 or a prefix
of one; a value is a reply dict, an exception to raise, or a callable ``(digests) -> dict | Exception``),
and every other call from ``text`` (a ``FakeModel`` or any callable ``(system, user, schema) -> dict``).
``calls`` keeps the order of the profiles (SC-005) and the digests of the images, never their bytes.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from pkpipe.ports import ImageInput, ImagesNotSupported, ModelError

Reply = dict[str, Any] | Exception | Callable[[list[str]], dict[str, Any] | Exception]


def valid_description(**overrides: Any) -> dict[str, Any]:
    """A reply that satisfies the ``image_description`` schema."""
    out: dict[str, Any] = {
        "kind": "screenshot",
        "topic": "A synthetic page about a fictional tool.",
        "visible_text": "Fictional Tool 1.0",
        "urls": [],
        "language": "en",
        "category_hint": "unknown",
        "injection_suspected": False,
        "evidence": None,
    }
    out.update(overrides)
    return out


class FakeVisionModel:
    def __init__(
        self,
        script: dict[str, Reply] | None = None,
        *,
        default: Reply | None = None,
        text: Callable[..., dict[str, Any]] | None = None,
        vision_profiles: set[str] | None = None,
        on_image_call: Callable[[], None] | None = None,
    ):
        self.script = dict(script or {})
        self.default = default
        self.text = text
        self.vision_profiles = {"vision"} if vision_profiles is None else vision_profiles
        self.on_image_call = on_image_call
        self.calls: list[dict[str, Any]] = []

    @property
    def profiles(self) -> list[str]:
        """The profile of every call in order."""
        return [c["profile"] for c in self.calls]

    @property
    def image_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["images"]]

    def switches(self) -> int:
        """How often consecutive calls change profile (SC-005: one switch per run)."""
        p = self.profiles
        return sum(1 for a, b in zip(p, p[1:], strict=False) if a != b)

    def _scripted(self, digests: list[str]) -> Reply:
        for key, reply in self.script.items():
            if any(d.startswith(key) for d in digests):
                return reply
        if self.default is not None:
            return self.default
        raise AssertionError(f"no scripted reply for image {digests}")

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
        if not digests:
            if self.text is None:
                raise AssertionError("FakeVisionModel has no text script")
            if getattr(self.text, "wants_schema", False):
                return self.text(system, user, json_schema)
            return self.text(system, user)
        if profile not in self.vision_profiles:
            raise ImagesNotSupported(f"profile '{profile}' does not declare vision: true")
        if self.on_image_call:
            self.on_image_call()
        reply = self._scripted(digests)
        if callable(reply):
            reply = reply(digests)
        if isinstance(reply, Exception):
            raise reply
        return dict(reply)


__all__ = ["FakeVisionModel", "ModelError", "valid_description"]
