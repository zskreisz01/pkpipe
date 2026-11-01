"""Unit tests never load a real model: the classifier factory (Qwen3Guard, DeBERTa) is replaced by the
substring-rule fake of ``tests.support.evals.fake_classifier`` (spec 009). Integration and live tests use the
real classifiers."""

from __future__ import annotations

import pytest

from tests.support.evals import fake_classifier


@pytest.fixture(autouse=True)
def _fake_classifier_factory(monkeypatch):
    import pkpipe.adapters.classifiers as classifiers

    monkeypatch.setattr(classifiers, "build_classifier", lambda cfg, environ=None: fake_classifier())
