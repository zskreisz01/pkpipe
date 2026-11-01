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
