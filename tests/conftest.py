"""Shared pytest setup: the ``live`` marker with auto-skip, plus synthetic-data fixtures.

Layout and markers are documented in tests/README.md.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.support import live

REPO = Path(__file__).resolve().parent.parent
PACK = REPO / "packs" / "learning-inbox"


def xdist_worker() -> str | None:
    """The pytest-xdist worker id (``gw0`` ...) of this process, or None in a plain (serial) run."""
    return os.environ.get("PYTEST_XDIST_WORKER")


def pytest_configure(config: pytest.Config) -> None:
    """No test (nor a CLI subprocess it starts) may use the real app DB: the data dir is a temp dir. Every
    pytest-xdist worker gets its own one, so parallel tests never share an app DB."""
    import tempfile

    if not os.environ.get("PKPIPE_TEST_DATA_DIR"):
        os.environ["PKPIPE_TEST_DATA_DIR"] = tempfile.mkdtemp(prefix="pkpipe-test-data-")
    if xdist_worker() and not os.environ.get("PKPIPE_TEST_DATA_WORKER"):
        # the controller set PKPIPE_TEST_DATA_DIR before it started the workers: nest one dir per worker
        os.environ["PKPIPE_TEST_DATA_DIR"] = tempfile.mkdtemp(
            prefix=f"{xdist_worker()}-", dir=os.environ["PKPIPE_TEST_DATA_DIR"]
        )
        os.environ["PKPIPE_TEST_DATA_WORKER"] = xdist_worker() or ""
    os.environ["PKPIPE_DATA_DIR"] = os.environ["PKPIPE_TEST_DATA_DIR"]
    os.environ.pop("PKPIPE_DB", None)


SERIAL_LIVE = (
    "live tests run serially: they share one LM Studio (and Lightpanda, Docker); run them without -n "
    "(PKPIPE_REQUIRE_LIVE=1 uv run pytest -q -m live) and the rest with -n auto -m 'not live'"
)


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip (or, with PKPIPE_REQUIRE_LIVE=1, fail) live tests whose local service is unreachable, and live
    tests in a pytest-xdist worker (parallel requests to the shared services; tests/README.md)."""
    marker = item.get_closest_marker("live")
    if marker is None:
        return
    if xdist_worker():
        if os.environ.get("PKPIPE_REQUIRE_LIVE") == "1":
            pytest.fail(SERIAL_LIVE, pytrace=False)
        pytest.skip(SERIAL_LIVE)
    reason = live.unavailable_reason(*marker.args)
    if reason is None:
        return
    if os.environ.get("PKPIPE_REQUIRE_LIVE") == "1":
        pytest.fail(f"PKPIPE_REQUIRE_LIVE=1 but live service unavailable: {reason}", pytrace=False)
    pytest.skip(f"live service unavailable: {reason}")


@pytest.fixture(scope="session")
def pack_dir() -> Path:
    """The public learning-inbox pack of this checkout."""
    return PACK


@pytest.fixture
def overlay_dir(tmp_path: Path) -> Path:
    """An empty synthetic private overlay; tests write the files they need."""
    d = tmp_path / "overlay"
    d.mkdir()
    return d


@pytest.fixture
def write_json():
    def _write(directory: Path, name: str, content: object) -> Path:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def synthetic_vault(tmp_path: Path) -> Path:
    """A temporary Obsidian-like vault with synthetic INBOX notes (never the owner's real vault)."""
    vault = tmp_path / "vault"
    for folder in ("0-INBOX", "0-INBOX/_quarantine", "3-Resources/Tools", "3-Resources/Learning"):
        (vault / folder).mkdir(parents=True)
    (vault / "0-INBOX" / "example-tool.md").write_text(
        "---\nsource: https://example.invalid/tool\n---\nA synthetic note about a fictional CLI tool.\n",
        encoding="utf-8",
    )
    (vault / "0-INBOX" / "example-video.md").write_text(
        "---\nsource: https://example.invalid/watch\n---\nA synthetic video worth a notebook.\n",
        encoding="utf-8",
    )
    return vault


@pytest.fixture
def lmstudio() -> dict[str, str]:
    """Base URL and model name of the live local model server; use together with @pytest.mark.live."""
    return {"base_url": live.model_base_url(), "model": live.model_name()}


@pytest.fixture
def otel():
    """In-memory telemetry (spans + metrics), installed as the active Telemetry; default content policy."""
    from pkpipe import telemetry
    from tests.support.otel import make_in_memory

    o = make_in_memory()
    telemetry.configure(o.telemetry)
    yield o
    telemetry.reset()
    o.telemetry.shutdown()


@pytest.fixture
def otel_factory():
    """Like ``otel`` but with a custom ContentPolicy: ``otel_factory(ContentPolicy(frozenset({'flow'})))``."""
    from pkpipe import telemetry
    from tests.support.otel import make_in_memory

    created = []

    def make(policy=None):
        o = make_in_memory(policy)
        telemetry.configure(o.telemetry)
        created.append(o)
        return o

    yield make
    telemetry.reset()
    for o in created:
        o.telemetry.shutdown()


@pytest.fixture
def stack(tmp_path: Path, synthetic_vault: Path, pack_dir: Path):
    """Config UI / app MCP stack on synthetic data (tests/support/app_stack.py)."""
    from tests.support.app_stack import make_stack

    return make_stack(tmp_path, synthetic_vault, pack_dir)
