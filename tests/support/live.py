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

DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL = "qwen3.5-9b-mlx"


def model_base_url() -> str:
    return os.environ.get("PKPIPE_MODEL_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def model_name() -> str:
    return os.environ.get("PKPIPE_MODEL_NAME", DEFAULT_MODEL)


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


PROBES: dict[str, Callable[[], str | None]] = {
    "lmstudio": _lmstudio,
    "docker": _docker,
    "lightpanda": _lightpanda,
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
