"""In-memory fakes for every port in ``pkpipe.ports`` (one fake per port, shared by all specs)."""

from tests.fakes.classifier import FakeClassifier
from tests.fakes.clock import FakeClock
from tests.fakes.config import InMemoryConfig
from tests.fakes.learning import FakeLearningBackend
from tests.fakes.model import FakeModel
from tests.fakes.notify import FakeNotify
from tests.fakes.runs import InMemoryRunStore
from tests.fakes.security_log import FakeSecurityLog
from tests.fakes.state import InMemoryState
from tests.fakes.tools import FakeTools
from tests.fakes.tracer import RecordedSpan, RecordingTracer
from tests.fakes.web import FakeSearch, FakeWeb

__all__ = [
    "FakeClassifier",
    "FakeClock",
    "FakeLearningBackend",
    "FakeModel",
    "FakeNotify",
    "FakeSearch",
    "FakeSecurityLog",
    "FakeTools",
    "FakeWeb",
    "InMemoryConfig",
    "InMemoryRunStore",
    "InMemoryState",
    "RecordedSpan",
    "RecordingTracer",
]
