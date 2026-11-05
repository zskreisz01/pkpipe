"""Spec 021: the composition root wires the image ports (preparer, description cache and timing table)."""

from __future__ import annotations

from pkpipe import app
from pkpipe.engine.registry import load_effective
from pkpipe.ports import ImagePreparePort, ImageStorePort


def test_ports_for_wires_the_image_adapters_to_the_app_db(pack_dir, tmp_path):
    snap = load_effective(pack_dir, None).snapshot()
    db = tmp_path / "pkpipe.db"
    ports = app.ports_for(snap, None, db)
    assert isinstance(ports.extra["image_preparer"], ImagePreparePort)
    store = ports.extra["image_store"]
    assert isinstance(store, ImageStorePort) and store.path == db
    store.cache_put("h", "m", "v", {"topic": "synthetic"})
    assert app.make_image_store(db).cache_get("h", "m", "v") == {"topic": "synthetic"}  # the same DB file


def test_the_run_service_hands_its_db_to_the_image_store(pack_dir, tmp_path):
    db = tmp_path / "run.db"
    service = app.build_run_service(pack_dir, None, db)
    ports = service.ports_for(service.config())
    assert ports.extra["image_store"].path == db
