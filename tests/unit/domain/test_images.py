"""Spec 021: the prepared-image buffer and the description model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pkpipe.domain.images import ImageBuffer, ImageDescription
from pkpipe.ports import PreparedImage


def prepared(n: int) -> PreparedImage:
    data = bytes([n]) * 10
    return PreparedImage(data=data, sha256=f"h{n}", width=1, height=1, source_format="PNG", source_bytes=10)


def test_the_buffer_is_a_bounded_lru():
    buf = ImageBuffer(max_entries=2)
    imgs = [prepared(i) for i in range(3)]
    for i, im in enumerate(imgs):
        buf.put(f"k{i}", im)
    assert buf.get("k0") is None and buf.get("k1") is imgs[1] and buf.get("k2") is imgs[2]
    buf.pop("k1")
    assert len(buf) == 1


def test_the_buffer_is_bounded_by_bytes_too_but_keeps_the_newest():
    buf = ImageBuffer(max_entries=10, max_bytes=25)
    for i in range(4):
        buf.put(f"k{i}", prepared(i))
    assert len(buf) == 2 and buf.get("k3") is not None


def test_the_prepared_image_repr_holds_no_bytes():
    out = prepared(7)
    assert "bytes" in repr(out) and out.data.hex()[:6] not in repr(out)


VALID = {
    "kind": "screenshot",
    "topic": "A synthetic page.",
    "visible_text": "Synthetic",
    "urls": [],
    "language": "en",
    "category_hint": "unknown",
    "injection_suspected": False,
    "evidence": None,
}


def test_a_valid_description_round_trips_and_has_no_confidence_field():
    d = ImageDescription.model_validate(VALID)
    assert d.model_dump() == VALID
    assert "confidence" not in ImageDescription.model_fields
    with pytest.raises(ValidationError):
        ImageDescription.model_validate(
            {**VALID, "confidence": 1.0}
        )  # the model's confidence is not accepted


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "painting"},
        {"visible_text": "x" * 401},
        {"topic": "x" * 301},
        {"urls": ["u"] * 11},
        {"injection_suspected": "yes"},
    ],
)
def test_an_invalid_description_is_refused(bad):
    with pytest.raises(ValidationError):
        ImageDescription.model_validate({**VALID, **bad})
