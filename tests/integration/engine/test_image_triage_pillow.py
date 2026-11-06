"""Spec 021 US2 scenario 3 and SC-002 with the real Pillow adapter: what the model receives has no EXIF, is
scaled and is a JPEG; twelve synthetic images through the engine give twelve valid descriptions."""

from __future__ import annotations

import io
import json

import pytest

from pkpipe.adapters.image_pillow import PillowPreparer
from tests.fakes.vision import valid_description
from tests.integration.engine.test_image_triage import ATT, rid
from tests.support import images
from tests.support.image_world import World

PIL = pytest.importorskip("PIL", reason="the optional extra 'images' (Pillow) is not installed")


class Recorder:
    """A vision model that keeps the image bytes it was sent and answers every image validly."""

    def __init__(self, text):
        self.text, self.sent = text, []

    def complete(self, system, user, json_schema=None, profile="default", images=None):
        if not images:
            return self.text(system, user, json_schema)
        self.sent.append(images[0])
        return valid_description(topic="A synthetic picture.", visible_text="x")


@pytest.fixture
def overlay(tmp_path):
    ov = tmp_path / "overlay"
    ov.mkdir()
    (ov / "layout.json").write_text(json.dumps({"inbox_list_file": "0-INBOX/INBOX.md"}))
    return ov


@pytest.fixture
def vault(tmp_path):
    """A vault whose list INBOX has real synthetic images only (the shared fixture's are byte strings)."""
    root = tmp_path / "vault"
    for f in ("0-INBOX", "4-Archive/Inbox", "3-Resources", "2-Areas", ATT):
        (root / f).mkdir(parents=True)
    (root / "0-INBOX" / "INBOX.md").write_text("# Inbox\n\n---\n", encoding="utf-8")
    return root


def _world(vault, pack_dir, overlay):
    w = World(vault, pack_dir, overlay)
    recorder = Recorder(w.model.text)
    w.model = recorder
    w.ports.model = recorder
    w.ports.extra["image_preparer"] = PillowPreparer()
    return w, recorder


def test_the_bytes_sent_to_the_model_have_no_exif_and_are_a_scaled_jpeg(vault, pack_dir, overlay):
    from PIL import Image

    raw = images.jpeg_with_exif(size=(3000, 1800))
    assert b"SyntheticCam" in raw and b"Exif" in raw
    (vault / ATT / "gps.jpg").write_bytes(raw)
    with (vault / "0-INBOX" / "INBOX.md").open("a", encoding="utf-8") as f:
        f.write("- ![[gps.jpg]]\n")
    w, rec = _world(vault, pack_dir, overlay)
    w.run()
    [sent] = [
        i for i in rec.sent if i.data[:2] == b"\xff\xd8" and Image.open(io.BytesIO(i.data)).size[0] == 1024
    ]
    assert sent.media_type == "image/jpeg"
    for marker in (b"Exif", b"SyntheticCam", b"synthetic-1.0"):
        assert marker not in sent.data
    assert not Image.open(io.BytesIO(sent.data)).getexif()
    assert w.rows[rid("gps.jpg")]["needs_owner"] is False


def test_an_unreadable_file_is_a_failed_image_not_a_failed_run(vault, pack_dir, overlay):
    (vault / ATT / "junk.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"not really a png")
    (vault / ATT / "bomb.png").write_bytes(images.png_header_only(100_000, 100_000))
    with (vault / "0-INBOX" / "INBOX.md").open("a", encoding="utf-8") as f:
        f.write("- ![[junk.png]]\n- ![[bomb.png]]\n")
    w, _ = _world(vault, pack_dir, overlay)
    w.run()
    assert "decode_failed" in w.rows[rid("junk.png")]["topic"] and w.rows[rid("junk.png")]["needs_owner"]
    assert "too_many_pixels" in w.rows[rid("bomb.png")]["topic"]


def test_twelve_synthetic_images_give_twelve_valid_descriptions(vault, pack_dir, overlay):
    """SC-002 on the fake model (the live repeat is tests/integration/test_images_live.py)."""
    for i in range(12):
        (vault / ATT / f"pic{i:02d}.png").write_bytes(images.text_image([f"synthetic picture number {i}"]))
    with (vault / "0-INBOX" / "INBOX.md").open("a", encoding="utf-8") as f:
        f.writelines(f"- ![[pic{i:02d}.png]]\n" for i in range(12))
    w, rec = _world(vault, pack_dir, overlay)
    run = w.run()
    rep = run.outputs["images"]["outputs"]["finish"]["report"]
    valid = [w.rows[rid(f"pic{i:02d}.png")] for i in range(12)]
    assert all(r["needs_owner"] is False and r["topic"] for r in valid)
    assert rep["failed"] == 0 and rep["described"] >= 12
