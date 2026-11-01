"""Bring the Open Notebook + Speaches stack up for live tests; stop it again if the tests started it."""

from __future__ import annotations

import subprocess
import time

from pkpipe.learning.open_notebook_setup import SetupOptions, run_setup, wait_healthy
from tests.support import live


class Stack:
    def __init__(self) -> None:
        self.started = False
        self.api_url = live.open_notebook_url()

    def ensure_up(self) -> None:
        if live.open_notebook_healthy():
            return
        folder = live.open_notebook_compose_dir()
        if folder is None:
            raise RuntimeError("no compose folder for Open Notebook")
        subprocess.run(
            ["docker", "compose", "up", "-d"], cwd=folder, check=True, capture_output=True, timeout=600
        )
        self.started = True
        self._folder = folder
        wait_healthy(self.api_url, timeout_s=180)
        time.sleep(2)

    def configure(self) -> dict[str, str]:
        """Idempotent: credentials, models, defaults and the pkpipe profiles."""
        return run_setup(SetupOptions(api_url=self.api_url), log=lambda m: None)

    def stop(self) -> None:
        if self.started:
            subprocess.run(["docker", "compose", "stop"], cwd=self._folder, capture_output=True, timeout=300)
