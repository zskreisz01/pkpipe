"""A scripted NotebookLM browser automation client for tests: no browser, no Google account (spec 012)."""

from __future__ import annotations

from pathlib import Path


class Driver:
    """A scripted browser automation client (no browser, no Google account)."""

    def __init__(self, **options):
        self.options = options
        self.calls = []
        self.state = ("generating", None)
        self.broken_at = None

    def _do(self, name, *args):
        self.calls.append((name, *args))
        if self.broken_at == name:
            raise TimeoutError("selector 'button[aria-label=Audio Overview]' not found")

    def create_notebook(self, title):
        self._do("create_notebook", title)
        return "nlm1"

    def add_url_source(self, notebook_id, url):
        self._do("add_url_source", notebook_id, url)

    def start_audio_overview(self, notebook_id, instructions):
        self._do("start_audio_overview", notebook_id, instructions)
        return "art1"

    def audio_state(self, notebook_id, artifact_id):
        self._do("audio_state", notebook_id, artifact_id)
        ready_after = self.options.get("ready_after")
        if ready_after is not None:  # progress by itself (a driver built from configuration)
            polls = sum(1 for c in self.calls if c[0] == "audio_state")
            return ("ready", None) if polls >= ready_after else ("generating", None)
        return self.state

    def notebook_url(self, notebook_id):
        return f"https://notebooklm.example.invalid/notebook/{notebook_id}"

    def download_audio(self, notebook_id, artifact_id, dest: Path):
        path = dest / "overview.m4a"
        path.write_bytes(b"audio")
        return path


def make(**options) -> Driver:
    """Driver factory in the ``module:factory`` form the backend configuration expects."""
    return Driver(**options)
