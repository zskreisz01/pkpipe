"""Spec 021 T005: the Pillow adapter of ``ImagePreparePort`` (EXIF removal, scaling, pixel guard, hash)."""

from __future__ import annotations

import hashlib
import io

import pytest

from pkpipe.adapters.image_pillow import PillowPreparer, prepare_image
from pkpipe.ports import ImageError, ImagePreparePort, PreparedImage
from tests.support import images

PIL = pytest.importorskip("PIL", reason="the optional extra 'images' (Pillow) is not installed")


def reopen(data: bytes):
    from PIL import Image

    return Image.open(io.BytesIO(data))


def test_a_jpeg_with_gps_exif_leaves_no_metadata():
    raw = images.jpeg_with_exif()
    assert b"Exif" in raw and b"SyntheticCam" in raw  # the fixture really has EXIF with the device
    out = prepare_image(raw)
    assert b"Exif" not in out.data and b"SyntheticCam" not in out.data and b"synthetic-1.0" not in out.data
    img = reopen(out.data)
    assert dict(img.getexif()) == {} and img.format == "JPEG"
    assert not img.info.get("exif") and not img.info.get("icc_profile")


def test_a_png_with_text_chunks_and_a_profile_is_clean_too():
    from PIL import Image, PngImagePlugin

    meta = PngImagePlugin.PngInfo()
    meta.add_text("Comment", "owner-secret-comment")
    out = io.BytesIO()
    Image.new("RGB", (40, 40), "red").save(out, format="PNG", pnginfo=meta)
    assert b"owner-secret-comment" in out.getvalue()
    prepared = prepare_image(out.getvalue())
    assert b"owner-secret-comment" not in prepared.data


def test_a_large_image_is_scaled_to_max_side_keeping_the_ratio():
    out = prepare_image(images.solid((3000, 1500)), max_side=1024)
    assert (out.width, out.height) == (1024, 512)
    assert max(reopen(out.data).size) == 1024


def test_a_small_image_is_not_enlarged():
    out = prepare_image(images.solid((200, 100)), max_side=1024)
    assert (out.width, out.height) == (200, 100)


def test_the_exif_orientation_is_applied_before_the_metadata_goes():
    from PIL import Image

    img = Image.new("RGB", (200, 100), "blue")
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise to display
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    out = prepare_image(buf.getvalue())
    assert (out.width, out.height) == (100, 200)


def test_the_hash_is_the_sha256_of_the_prepared_bytes_and_stable():
    raw = images.text_image(["same image"])
    a, b = prepare_image(raw), prepare_image(raw)
    assert a.sha256 == hashlib.sha256(a.data).hexdigest() == b.sha256
    assert a.sha256 != prepare_image(images.text_image(["another image"])).sha256


def test_transparency_is_composited_onto_white_and_modes_become_rgb():
    out = prepare_image(images.solid((10, 10), (0, 0, 0, 0), mode="RGBA"))
    assert reopen(out.data).mode == "RGB"
    assert reopen(out.data).getpixel((5, 5))[0] > 240  # fully transparent: white
    assert reopen(prepare_image(images.solid((10, 10), 128, mode="L")).data).mode == "RGB"


def test_an_animated_gif_gives_its_first_frame():
    from PIL import Image

    frames = [Image.new("RGB", (30, 30), c) for c in ("red", "green")]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:])
    assert (prepare_image(buf.getvalue()).width) == 30


def test_a_decompression_bomb_is_refused_from_its_header():
    bomb = images.png_header_only(100_000, 100_000)  # ten gigapixels announced, no pixel data at all
    with pytest.raises(ImageError) as exc:
        prepare_image(bomb)
    assert exc.value.code == "too_many_pixels"


def test_the_pixel_limit_is_a_parameter():
    raw = images.solid((100, 100))
    assert prepare_image(raw, max_pixels=10_000).width == 100
    with pytest.raises(ImageError) as exc:
        prepare_image(raw, max_pixels=9_999)
    assert exc.value.code == "too_many_pixels"


@pytest.mark.parametrize(
    "junk", [b"", b"not an image", b"\x89PNG\r\n\x1a\n" + b"\x00" * 30, b"\xff\xd8\xff\xe0trunc"]
)
def test_junk_is_a_stable_error_without_content(junk):
    with pytest.raises(ImageError) as exc:
        prepare_image(junk)
    assert exc.value.code in ("empty", "decode_failed")
    assert "not an image" not in str(exc.value)


def test_the_prepared_image_repr_holds_no_bytes():
    out = prepare_image(images.solid((20, 20)))
    assert isinstance(out, PreparedImage) and "bytes" in repr(out) and out.sha256[:12] in repr(out)
    assert out.data.hex()[:20] not in repr(out)


def test_without_pillow_the_error_names_the_extra(monkeypatch):
    import builtins

    real = builtins.__import__

    def deny(name, *a, **k):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("no pillow")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", deny)
    with pytest.raises(ImageError) as exc:
        prepare_image(b"x")
    assert exc.value.code == "pillow_missing" and "extra" in str(exc.value)


def test_the_adapter_is_the_port():
    assert isinstance(PillowPreparer(), ImagePreparePort)
    out = PillowPreparer().prepare(images.solid((2000, 1000)), max_side=500, max_pixels=10_000_000)
    assert max(out.width, out.height) == 500


def test_a_jpeg_comment_and_a_gif_comment_do_not_survive():
    from PIL import Image

    jpeg = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(jpeg, format="JPEG", comment=b"owner-secret-jpeg-comment")
    assert b"owner-secret-jpeg-comment" in jpeg.getvalue()
    gif = io.BytesIO()
    Image.new("RGB", (40, 30), "blue").save(gif, format="GIF", comment=b"owner-secret-gif-comment")
    assert b"owner-secret-gif-comment" in gif.getvalue()
    for raw in (jpeg.getvalue(), gif.getvalue()):
        out = prepare_image(raw)
        assert b"owner-secret" not in out.data
        assert not reopen(out.data).info.get("comment")
        assert set(reopen(out.data).info) <= {"jfif", "jfif_version", "jfif_unit", "jfif_density", "dpi"}
