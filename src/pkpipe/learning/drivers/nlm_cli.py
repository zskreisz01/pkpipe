"""NotebookLM driver on top of the ``nlm`` command line tool (notebooklm-mcp-cli), spec 032.

The owner logs in once with ``nlm login`` (their own Google account); this module only runs ``nlm`` and never
sees, prints or stores a credential or a cookie. It implements the :class:`NotebookLMDriver` protocol of the
backend plus the optional ``usage()`` the backend uses to check the quota before it creates anything.

Select it in ``mcp_servers.json`` (the pack does by default)::

    "learning": {"backend": "notebooklm_browser",
                 "options": {"backend_options": {"driver": "pkpipe.learning.drivers.nlm_cli:make_driver"}}}
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

Runner = Callable[[list[str], float], str]
FOCUS_MAX_CHARS = 1500
TITLE_MAX_CHARS = 90
MESSAGE_CHARS = 200


class NlmError(RuntimeError):
    """A failed ``nlm`` call; the message is the last line of its output (the backend classifies it)."""


def _run(args: list[str], timeout: float) -> str:
    exe = shutil.which("nlm") or str(Path.home() / ".local/bin/nlm")
    try:
        proc = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise NlmError(f"nlm {args[0]} {args[1] if len(args) > 1 else ''} timed out") from None
    except FileNotFoundError:
        raise NlmError("nlm is not installed (uv tool install notebooklm-mcp-cli)") from None
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout or "nlm failed").strip().splitlines() or ["nlm failed"]
        raise NlmError(lines[-1][:MESSAGE_CHARS])
    return proc.stdout


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise NlmError("nlm returned no JSON") from None


class NlmDriver:
    def __init__(self, run: Runner = _run, profile: str | None = None, timeout_s: float = 110.0):
        self._run = run
        self._profile = ["--profile", profile] if profile else []
        self._timeout = timeout_s

    def _call(self, *args: str) -> str:
        return self._run([*args, *self._profile], self._timeout)

    def _audio_artifacts(self, notebook_id: str) -> list[dict[str, Any]]:
        rows = _json(self._call("studio", "status", notebook_id, "--json"))
        return [r for r in rows if isinstance(r, dict) and r.get("type") == "audio"]

    def usage(self) -> dict[str, Any]:
        """``nlm usage --json``: ``{"windows": [{"window", "percent_remaining", "resets_at"}], "tier"}``."""
        data = _json(self._call("usage", "--json"))
        return data if isinstance(data, dict) else {}

    def create_notebook(self, title: str) -> str:
        data = _json(self._call("notebook", "create", title, "--json"))
        return str(data["notebook_id"])

    def add_url_source(self, notebook_id: str, url: str) -> None:
        out = self._call("source", "add", notebook_id, "--url", url, "--wait", "--json")
        # a notebook named "pkpipe <id>" says nothing: take the title of the source (best effort)
        try:
            title = " ".join(str(_json(out).get("title") or "").split())[:TITLE_MAX_CHARS]
            if title:
                self._call("notebook", "rename", notebook_id, title)
        except (NlmError, AttributeError):
            pass

    def start_audio_overview(self, notebook_id: str, instructions: str) -> str:
        before = {str(a.get("artifact_id") or a.get("id")) for a in self._audio_artifacts(notebook_id)}
        args = ["audio", "create", notebook_id, "--format", "deep_dive", "--confirm", "--json"]
        focus = " ".join((instructions or "").split())[:FOCUS_MAX_CHARS]
        if focus:
            args += ["--focus", focus]
        out = self._call(*args)
        try:
            data = _json(out)
            found = data.get("artifact_id") or data.get("id") if isinstance(data, dict) else None
        except NlmError:
            found = None
        if found:
            return str(found)
        new = [
            a
            for a in self._audio_artifacts(notebook_id)
            if str(a.get("artifact_id") or a.get("id")) not in before
        ]
        if not new:
            raise NlmError("NotebookLM did not report the new audio overview")
        return str(new[-1].get("artifact_id") or new[-1].get("id"))

    def audio_state(self, notebook_id: str, artifact_id: str) -> tuple[str, str | None]:
        for a in self._audio_artifacts(notebook_id):
            if str(a.get("artifact_id") or a.get("id")) == artifact_id:
                status = str(a.get("status") or "").lower()
                if status in ("completed", "ready", "done"):
                    return "ready", None
                if status in ("failed", "error"):
                    return "failed", "NotebookLM could not generate the audio overview"
                return "generating", None
        return "failed", "the audio overview is gone from the notebook"

    def notebook_url(self, notebook_id: str) -> str:
        return f"https://notebook.google.com/notebook/{notebook_id}"

    def download_audio(self, notebook_id: str, artifact_id: str, dest: Path) -> Path:
        """``dest`` is the job's audio directory; NotebookLM audio is MPEG-4 audio, so the file is
        ``<artifact>.m4a`` (``nlm download audio`` refuses ``.mp3``). ``nlm`` also refuses to write below some
        application directories, so the file is downloaded into a temporary directory and moved."""
        if dest.suffix.lower() in (".m4a", ".mp3", ".mp4") and not dest.is_dir():
            target = dest.with_suffix(".m4a")
        else:
            safe = "".join(c if c.isalnum() or c in "_.-" else "_" for c in artifact_id)
            target = dest / f"{safe}.m4a"
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="pkpipe-nlm-") as tmp:
            part = Path(tmp) / "audio.m4a"
            self._call(
                "download", "audio", notebook_id, "--id", artifact_id, "--output", str(part), "--no-progress"
            )
            if not part.exists() or part.stat().st_size == 0:
                raise NlmError("the downloaded audio file is empty")
            shutil.move(str(part), target)
        return target


def make_driver(profile: str | None = None, timeout_s: float = 110.0) -> NlmDriver:
    return NlmDriver(profile=profile, timeout_s=timeout_s)
