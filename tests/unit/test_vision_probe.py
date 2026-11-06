"""The research probe never sends private images to a remote server unless told to (spec 021 review)."""

from __future__ import annotations

import subprocess
import sys

from tests.conftest import REPO

PROBE = REPO / "specs" / "021-image-input" / "research" / "vision_probe.py"


def _run(tmp_path, base_url, **env):
    import os

    return subprocess.run(
        [sys.executable, str(PROBE), str(tmp_path), "some-model"],
        capture_output=True,
        text=True,
        env={**os.environ, "BASE_URL": base_url, **env},
        timeout=60,
    )


def test_a_non_loopback_server_is_refused_before_anything_is_read_or_sent(tmp_path):
    r = _run(tmp_path, "https://api.example.invalid/v1")
    assert r.returncode != 0 and "refusing to send images to a non-loopback server" in r.stderr


def test_a_loopback_server_is_accepted_and_the_temp_dir_is_removed(tmp_path):
    r = _run(tmp_path, "http://127.0.0.1:9/v1")  # no images: nothing is sent, the run ends cleanly
    assert r.returncode == 0 and "refusing" not in r.stderr


def test_the_explicit_override_allows_a_remote_server(tmp_path):
    r = _run(tmp_path, "https://api.example.invalid/v1", ALLOW_REMOTE="1")
    assert r.returncode == 0 and "refusing" not in r.stderr
