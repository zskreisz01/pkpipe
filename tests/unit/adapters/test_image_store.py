"""Spec 021 FR-012, FR-014: the description cache and the timing table (``ImageStorePort``): one contract for
the fake and the app DB adapter."""

from __future__ import annotations

import pytest

from pkpipe.adapters.app_store_sqlite import AppStore
from pkpipe.ports import ImageStorePort
from tests.fakes import FakeImageStore


@pytest.fixture(params=["fake", "sqlite"])
def store(request, tmp_path):
    return FakeImageStore() if request.param == "fake" else AppStore(tmp_path / "app.db")


DESC = {"kind": "screenshot", "topic": "A synthetic page.", "visible_text": "Synthetic", "urls": []}


def test_it_is_the_port(store):
    assert isinstance(store, ImageStorePort)


def test_the_cache_key_is_hash_model_and_prompt_version(store):
    assert store.cache_get("h1", "m", "v1") is None
    store.cache_put("h1", "m", "v1", DESC)
    assert store.cache_get("h1", "m", "v1") == DESC
    assert store.cache_get("h1", "m", "v2") is None  # a changed prompt is a new description
    assert store.cache_get("h1", "other-model", "v1") is None
    assert store.cache_get("h2", "m", "v1") is None


def test_putting_again_replaces_the_description(store):
    store.cache_put("h1", "m", "v1", DESC)
    store.cache_put("h1", "m", "v1", {**DESC, "topic": "Changed."})
    assert store.cache_get("h1", "m", "v1")["topic"] == "Changed."


def test_the_cache_hands_out_copies(store):
    store.cache_put("h1", "m", "v1", {"urls": ["a"]})
    got = store.cache_get("h1", "m", "v1")
    got["urls"].append("b")
    assert store.cache_get("h1", "m", "v1") == {"urls": ["a"]}


def test_recent_seconds_are_the_last_rows_of_the_model_newest_first(store):
    for i, sec in enumerate([10.0, 20.0, 30.0, 40.0]):
        store.record_timing(f"run-{i}", f"h{i}", "m", sec)
    store.record_timing("run-x", "hx", "other", 999.0)
    assert store.recent_seconds("m", limit=3) == [40.0, 30.0, 20.0]
    assert store.recent_seconds("m") == [40.0, 30.0, 20.0, 10.0]
    assert store.recent_seconds("nobody") == []


def test_the_app_db_keeps_the_rows_across_connections(tmp_path):
    path = tmp_path / "app.db"
    AppStore(path).cache_put("h", "m", "v", DESC)
    AppStore(path).record_timing("r", "h", "m", 12.5)
    again = AppStore(path)  # a new process: the tables exist already (CREATE IF NOT EXISTS)
    assert again.cache_get("h", "m", "v") == DESC and again.recent_seconds("m") == [12.5]


def test_get_latest_ignores_the_prompt_version_but_not_hash_or_model(store):
    """Spec 028: a caller that makes no model call takes the newest description of the model."""
    assert store.cache_get_latest("h1", "m") is None
    store.cache_put("h1", "m", "v1", {**DESC, "topic": "Old."})
    store.cache_put("h1", "m", "v2", {**DESC, "topic": "New."})
    assert store.cache_get_latest("h1", "m")["topic"] == "New."
    assert store.cache_get_latest("h1", "other-model") is None
    assert store.cache_get_latest("h2", "m") is None
