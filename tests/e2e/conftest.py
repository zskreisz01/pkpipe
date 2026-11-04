"""End-to-end runs use the real composition root (``pkpipe.app``). The local classifier (Qwen3Guard +
DeBERTa, spec 009) is replaced by the substring-rule fake, so a machine without the models still runs the
flows exactly; tests marked ``real_classifier`` keep the real one (they also need ``live("guard")``)."""

from __future__ import annotations

import pytest

from tests.support.evals import fake_classifier


@pytest.fixture(autouse=True)
def _fake_classifier_at_the_composition_root(request, monkeypatch):
    if request.node.get_closest_marker("real_classifier"):
        return
    from pkpipe import app

    monkeypatch.setattr(app, "make_classifier", lambda snapshot: fake_classifier())


@pytest.fixture(autouse=True)
def _the_engine_knows_the_vault(request, monkeypatch):
    """A flow that can write to the vault does not start without a snapshot (spec 019 FR-001), and the
    snapshot needs the vault in the process that runs the flow (``PKPIPE_VAULT_DIR``, as in production)."""
    if "synthetic_vault" in request.fixturenames:
        monkeypatch.setenv("PKPIPE_VAULT_DIR", str(request.getfixturevalue("synthetic_vault")))
        monkeypatch.setenv("PKPIPE_DATA_DIR", str(request.getfixturevalue("tmp_path") / "snapshot-data"))
