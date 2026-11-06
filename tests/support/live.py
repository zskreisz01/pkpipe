"""Probes for local services needed by ``@pytest.mark.live`` tests.

``@pytest.mark.live`` (default service: lmstudio) or ``@pytest.mark.live("docker", "lightpanda")``.
Unreachable service -> the test is skipped (keeps GitHub CI green). Set ``PKPIPE_REQUIRE_LIVE=1`` to turn
the skip into a failure (use this on the machine that owns the services).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from functools import cache
from pathlib import Path

REPO_DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "open-notebook"
PACK_DIR = Path(__file__).resolve().parents[2] / "packs" / "learning-inbox"
DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL = "qwen3.5-9b-mlx"
DEFAULT_JUDGE_MODEL = "google/gemma-4-12b"
DEFAULT_VISION_MODEL = "google/gemma-4-12b"
#: The most eval requests in flight in live tests: LM Studio serves them with parallel predictions
#: (``scripts/lmstudio-test-setup.sh`` loads both models with ``--parallel 4``). The session's system
#: preflight may lower it (:func:`eval_concurrency`). Production runs default to ``evals.concurrency`` = 1.
LIVE_EVAL_CONCURRENCY = 4


def eval_concurrency() -> int:
    """Eval requests in flight for the next live eval (``EvalRunner(concurrency=...)``): the current system
    preflight decision (tests/support/preflight.py), else :data:`LIVE_EVAL_CONCURRENCY`."""
    from tests.support import preflight

    decision = preflight.session.decision
    if decision is None or decision.concurrency < 1:
        return LIVE_EVAL_CONCURRENCY
    return min(LIVE_EVAL_CONCURRENCY, decision.concurrency)


def model_base_url() -> str:
    return os.environ.get("PKPIPE_MODEL_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def model_name() -> str:
    return os.environ.get("PKPIPE_MODEL_NAME", DEFAULT_MODEL)


def judge_model_name() -> str:
    """The eval judge (spec 013): a different model than the one under test."""
    return os.environ.get("PKPIPE_JUDGE_MODEL", DEFAULT_JUDGE_MODEL)


def judge_base_url() -> str:
    """The judge's endpoint, resolved like the pack's ``judge`` profile (``models.json``):
    ``PKPIPE_JUDGE_BASE_URL``, else the model URL (one server serving both models)."""
    return (os.environ.get("PKPIPE_JUDGE_BASE_URL") or model_base_url()).rstrip("/")


@cache
def served_models(base_url: str) -> tuple[str, ...]:
    """Model ids served at ``<base_url>/models``, or () when the server is unreachable."""
    try:
        with urllib.request.urlopen(f"{base_url}/models", timeout=2) as resp:
            payload = json.load(resp)
        return tuple(m["id"] for m in payload.get("data", []))
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return ()


def lmstudio_models() -> tuple[str, ...]:
    """Model ids served at the model URL (``PKPIPE_MODEL_BASE_URL``), or () when unreachable."""
    return served_models(model_base_url())


def _lmstudio() -> str | None:
    models = lmstudio_models()
    if not models:
        return f"LM Studio not reachable at {model_base_url()}"
    if model_name() not in models:
        return f"model '{model_name()}' is not loaded in LM Studio (have: {', '.join(models)})"
    return None


def vision_model_name() -> str:
    """The model of the pack's ``vision`` profile (spec 021): ``PKPIPE_VISION_MODEL``, else gemma-4-12b."""
    return os.environ.get("PKPIPE_VISION_MODEL", DEFAULT_VISION_MODEL)


def _vision() -> str | None:
    """LM Studio serves the vision model (image description, spec 021)."""
    models = lmstudio_models()
    if not models:
        return f"LM Studio not reachable at {model_base_url()}"
    if vision_model_name() not in models:
        return f"vision model '{vision_model_name()}' is not loaded in LM Studio (have: {', '.join(models)})"
    return None


def _judge() -> str | None:
    """The model under test and the judge, each at its own endpoint: a one-model-per-port server (vLLM,
    ``PKPIPE_JUDGE_BASE_URL``) lists only the judge at the judge's port."""
    reason = _lmstudio()
    if reason:
        return reason
    if judge_model_name() == model_name():
        return "judge model equals the model under test (set PKPIPE_JUDGE_MODEL)"
    url = judge_base_url()
    models = served_models(url)
    if not models:
        return f"judge server not reachable at {url}"
    if judge_model_name() not in models:
        return f"judge model '{judge_model_name()}' is not available at {url} (have: {', '.join(models)})"
    return None


@cache
def _docker() -> str | None:
    if not shutil.which("docker"):
        return "docker not installed"
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "docker daemon not reachable"
    return None if r.returncode == 0 else "docker daemon not reachable"


def _lightpanda() -> str | None:
    return (
        None if shutil.which("lightpanda") or os.environ.get("LIGHTPANDA_BIN") else "lightpanda not installed"
    )


@cache
def _browser() -> str | None:
    """Playwright with an installed Chromium (``uv run playwright install chromium``)."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Exception as exc:  # noqa: BLE001 - any failure means "no usable browser"
        return f"Playwright Chromium not available ({type(exc).__name__}): uv run playwright install chromium"
    return None


def open_notebook_url() -> str:
    return os.environ.get("PKPIPE_OPEN_NOTEBOOK_URL", "http://127.0.0.1:5055").rstrip("/")


def open_notebook_healthy() -> bool:
    try:
        with urllib.request.urlopen(f"{open_notebook_url()}/health", timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def open_notebook_compose_dir() -> Path | None:
    """The compose folder the tests may start: ``PKPIPE_OPEN_NOTEBOOK_DIR``, the repo's deploy folder when it
    has a ``.env``, or the owner's prepared ``~/tools/open-notebook``."""
    candidates = [os.environ.get("PKPIPE_OPEN_NOTEBOOK_DIR"), str(REPO_DEPLOY), "~/tools/open-notebook"]
    for c in filter(None, candidates):
        folder = Path(c).expanduser()
        if (folder / "docker-compose.yml").is_file() and (
            folder != REPO_DEPLOY or (folder / ".env").is_file()
        ):
            return folder
    return None


def _open_notebook() -> str | None:
    """Open Notebook + Speaches: running already, or startable (Docker and a prepared compose folder)."""
    if open_notebook_healthy():
        return None
    reason = _docker()
    if reason:
        return f"Open Notebook not reachable at {open_notebook_url()} and {reason}"
    if open_notebook_compose_dir() is None:
        return (
            f"Open Notebook not reachable at {open_notebook_url()} and no compose folder found "
            "(deploy/open-notebook/.env or PKPIPE_OPEN_NOTEBOOK_DIR)"
        )
    return None


QWEN3GUARD_DIR = Path("~/tools/qwen3guard/Qwen3Guard-Gen-0.6B").expanduser()
DEBERTA_DIR = Path("~/tools/protectai-deberta-pi-v2").expanduser()


@cache
def _guard() -> str | None:
    """The local classifiers of spec 008/009 (docs/decisions/0001): both model directories and the ``guard``
    extra (torch, transformers)."""
    import importlib.util

    for directory in (QWEN3GUARD_DIR, DEBERTA_DIR):
        if not (directory / "config.json").is_file() or not (directory / "model.safetensors").is_file():
            return f"classifier model not installed at {directory} (see docs/installation.md)"
    if not (importlib.util.find_spec("torch") and importlib.util.find_spec("transformers")):
        return "optional extra missing: uv sync --extra guard"
    return None


PROBES: dict[str, Callable[[], str | None]] = {
    "guard": _guard,
    "open_notebook": _open_notebook,
    "lmstudio": _lmstudio,
    "judge": _judge,
    "vision": _vision,
    "docker": _docker,
    "lightpanda": _lightpanda,
    "browser": _browser,
}


def unavailable_reason(*services: str) -> str | None:
    """First reason why one of the services is unavailable, or None when all are usable."""
    for name in services or ("lmstudio",):
        if name not in PROBES:
            raise KeyError(f"unknown live service '{name}' (known: {sorted(PROBES)})")
        reason = PROBES[name]()
        if reason:
            return reason
    return None
