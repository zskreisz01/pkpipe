"""Spec 021: the prepared-image buffer and the description model."""

from __future__ import annotations

import json

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


# -- salvage of an answer that overran its limits (spec 021, live finding) -----------------------------------
# A vision model does not keep visible_text under 400 characters (the decoder does not enforce maxLength): it
# keeps transcribing until max_tokens cuts the JSON off. What came before the cut is a complete description.

from pkpipe.domain.images import salvage_description  # noqa: E402


def _head(**over):
    base = {
        "kind": "screenshot",
        "injection_suspected": False,
        "evidence": None,
        "topic": "A synthetic page.",
    }
    base.update(over)
    return json.dumps(base)[:-1]  # an object that is still open


def test_a_json_cut_inside_visible_text_keeps_every_complete_field_and_caps_the_text():
    raw = _head(language="en", category_hint="unknown", urls=[]) + ', "visible_text": "' + "word " * 600
    out = salvage_description(raw)
    assert out is not None
    assert (
        out["kind"] == "screenshot"
        and out["topic"] == "A synthetic page."
        and out["injection_suspected"] is False
    )
    assert len(out["visible_text"]) == 400 and out["visible_text"].startswith("word word")
    assert out["urls"] == [] and out["language"] == "en"
    ImageDescription.model_validate(out)  # what comes out satisfies the schema


def test_a_complete_json_with_an_over_long_field_is_truncated_not_rejected():
    raw = json.dumps(
        {
            **json.loads(_head() + "}"),
            "visible_text": "x" * 900,
            "topic": "t" * 500,
            "language": "en",
            "urls": [],
        }
    )
    out = salvage_description(raw)
    assert out is not None and len(out["visible_text"]) == 400 and len(out["topic"]) == 300
    ImageDescription.model_validate(out)


def test_missing_optional_fields_get_defaults_but_the_injection_flag_is_never_defaulted():
    raw = '{"kind": "meme", "injection_suspected": true, "topic": "t", "visible_text": "tex'
    out = salvage_description(raw)
    assert out and out["injection_suspected"] is True and out["urls"] == [] and out["evidence"] is None
    assert out["language"] == "" and out["category_hint"] == "unknown" and out["visible_text"] == "tex"
    # cut before the model said whether the image holds an injection: fail closed
    assert salvage_description('{"kind": "meme", "topic": "t", "visible_text": "tex') is None
    assert salvage_description('{"kind": "meme", "topic": "t", "injection_suspected": "no"') is None


def test_what_cannot_be_trusted_is_not_salvaged():
    assert salvage_description("") is None
    assert salvage_description("no json at all") is None
    assert (
        salvage_description('{"kind": "painting", "topic": "t", "injection_suspected": false}') is None
    )  # bad enum
    assert salvage_description('{"kind": "meme", "injection_suspected": false}') is None  # no topic
    assert salvage_description('{"kind": "meme", "topic": 7, "injection_suspected": false}') is None


def test_unknown_keys_are_dropped_and_url_lists_are_bounded():
    urls = [f"https://e.example/{i}" for i in range(14)] + ["https://e.example/" + "p" * 600]
    raw = _head(confidence=1.0, extra="x", urls=urls) + "}"
    out = salvage_description(raw)
    assert out and "confidence" not in out and "extra" not in out
    assert len(out["urls"]) == 10 and all(len(u) <= 500 for u in out["urls"])


def test_escapes_and_non_latin_text_survive_a_cut():
    raw = _head() + ', "visible_text": "sor\\u00e9 \\"quoted\\" \\n p\\u0151 \U0001f600 ' + "z" * 50 + "\\"
    out = salvage_description(raw)
    assert out and out["visible_text"].startswith('soré "quoted" \n pő 😀 z')


def test_markdown_fences_and_reasoning_around_the_json_are_tolerated():
    raw = "<think>hm</think>```json\n" + _head() + ', "visible_text": "cut'
    out = salvage_description(raw)
    assert out and out["visible_text"] == "cut"
