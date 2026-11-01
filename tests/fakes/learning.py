"""Fake learning backend (spec 012 T002): the scripted ``pkpipe.learning.backends.FakeBackend`` is also the
runtime ``fake`` backend, so tests and the ``backend: fake`` configuration run the same code."""

from __future__ import annotations

from pkpipe.learning.backends import FakeBackend as FakeLearningBackend

__all__ = ["FakeLearningBackend"]
