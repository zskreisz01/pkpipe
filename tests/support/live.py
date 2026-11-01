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
DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL = "qwen3.5-9b-mlx"
DEFAULT_JUDGE_MODEL = "google/gemma-4-12b"


def model_base_url() -> str:
    return os.environ.get("PKPIPE_MODEL_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def model_name() -> str:
    return os.environ.get("PKPIPE_MODEL_NAME", DEFAULT_MODEL)


def judge_model_name() -> str:
    """The eval judge (spec 013): a different model than the one under test."""
    return os.environ.get("PKPIPE_JUDGE_MODEL", DEFAULT_JUDGE_MODEL)


@cache
def lmstudio_models() -> tuple[str, ...]:
    """Model ids served at /v1/models, or () when the server is unreachable."""
    try:
        with urllib.request.urlopen(f"{model_base_url()}/models", timeout=2) as resp:
            payload = json.load(resp)
        return tuple(m["id"] for m in payload.get("data", []))
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return ()


def _lmstudio() -> str | None:
    models = lmstudio_models()
    if not models:
        return f"LM Studio not reachable at {model_base_url()}"
    if model_name() not in models:
        return f"model '{model_name()}' is not loaded in LM Studio (have: {', '.join(models)})"
    return None


def _judge() -> str | None:
    reason = _lmstudio()
    if reason:
        return reason
    if judge_model_name() == model_name():
        return "judge model equals the model under test (set PKPIPE_JUDGE_MODEL)"
    if judge_model_name() not in lmstudio_models():
        return f"judge model '{judge_model_name()}' is not available in LM Studio"
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


PROBES: dict[str, Callable[[], str | None]] = {
    "open_notebook": _open_notebook,
    "lmstudio": _lmstudio,
    "judge": _judge,
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
