"""Open Notebook backend (REST, spec 012 T003): notebook + link source + podcast generation with local TTS.

DEPRECATED (spec 032): ``notebooklm_browser`` is the supported learning backend (better results, no local TTS
to maintain). This adapter stays for owners who run Open Notebook; it is not the default and gets no new
features.

Endpoints used: ``POST /api/notebooks``, ``POST /api/sources/json``, ``POST /api/podcasts/generate``,
``GET /api/podcasts/jobs/{id}``, ``GET /api/podcasts/episodes/{id}/audio``. The models, voices and the
episode / speaker profiles are configured once by ``deploy/open-notebook/setup.py``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from ..backend import BackendHandle, BackendStatus
from ..errors import BackendUnavailable
from ..models import BackendName, JobStatus

DEFAULT_API = "http://127.0.0.1:5055"
DEFAULT_UI = "http://127.0.0.1:8502"
_RUNNING = {"submitted", "pending", "queued", "running", "started", "processing"}
_DONE = {"completed", "success", "succeeded"}


class OpenNotebookBackend:
    name = BackendName.OPEN_NOTEBOOK

    def __init__(
        self,
        api_url: str = DEFAULT_API,
        ui_url: str = DEFAULT_UI,
        *,
        episode_profile: str = "pkpipe_overview",
        speaker_profile: str = "pkpipe_duo",
        password_env: str | None = "OPEN_NOTEBOOK_PASSWORD",
        request_timeout_s: float = 110.0,
        environ: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.api_url = api_url.rstrip("/")
        self.ui_url = ui_url.rstrip("/")
        self.episode_profile = episode_profile
        self.speaker_profile = speaker_profile
        env = os.environ if environ is None else environ
        headers = {}
        password = env.get(password_env) if password_env else None
        if password:  # only when Open Notebook has authentication enabled; never logged or echoed
            headers["Authorization"] = f"Bearer {password}"
        self._client = httpx.Client(
            base_url=self.api_url, headers=headers, timeout=request_timeout_s, transport=transport
        )

    # -- http ------------------------------------------------------------------------------------------
    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            resp = self._client.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise BackendUnavailable(
                f"Open Notebook not reachable at {self.api_url} ({type(exc).__name__})"
            ) from None
        if resp.status_code >= 400:
            raise BackendUnavailable(
                f"Open Notebook {method} {path} failed: HTTP {resp.status_code}{_detail(resp)}"
            )
        return resp

    def _json(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        try:
            data = self._request(method, path, **kw).json()
        except ValueError:
            raise BackendUnavailable(f"Open Notebook {path} returned no JSON") from None
        if not isinstance(data, dict):
            raise BackendUnavailable(f"Open Notebook {path} returned an unexpected payload")
        return data

    # -- LearningBackend -------------------------------------------------------------------------------
    def create(self, url: str, template: str, item_id: str) -> BackendHandle:
        label = f"pkpipe {item_id}".strip()
        notebook = self._json(
            "POST", "/api/notebooks", json={"name": label, "description": "created by pkpipe"}
        )
        nb_id = _need(notebook, "id", "notebook")
        self._json(
            "POST",
            "/api/sources/json",
            json={
                "type": "link",
                "url": url,
                "notebooks": [nb_id],
                "embed": False,
                "async_processing": False,
            },
        )
        payload: dict[str, Any] = {
            "episode_profile": self.episode_profile,
            "speaker_profile": self.speaker_profile,
            "episode_name": label,
            "notebook_id": nb_id,
        }
        if template:
            payload["briefing_suffix"] = template
        job = self._json("POST", "/api/podcasts/generate", json=payload)
        return BackendHandle(nb_id=nb_id, job_id=_need(job, "job_id", "podcast job"))

    def poll(self, handle: BackendHandle) -> BackendStatus:
        data = self._json("GET", f"/api/podcasts/jobs/{quote(handle.job_id, safe='')}")
        state = str(data.get("status") or "").lower()
        if state in _DONE:
            result = data.get("result") or {}
            episode = result.get("episode_id") if isinstance(result, dict) else None
            if not episode:
                return BackendStatus(JobStatus.FAILED, "Open Notebook finished without an episode")
            return BackendStatus(JobStatus.READY, ref=str(episode))
        if state in _RUNNING or not state:
            return BackendStatus(JobStatus.GENERATING)
        reason = str(data.get("error_message") or f"Open Notebook job {state}")
        return BackendStatus(JobStatus.FAILED, _first_line(reason))

    def link(self, nb_id: str) -> str:
        return f"{self.ui_url}/notebooks/{quote(nb_id, safe='')}"

    def fetch_audio(self, handle: BackendHandle, dest: Path) -> Path:
        if not handle.ref:
            raise BackendUnavailable("no episode id for this job")
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", handle.ref)
        path = dest / f"{safe}.mp3"
        tmp = path.with_suffix(".part")
        try:
            with self._client.stream(
                "GET", f"/api/podcasts/episodes/{quote(handle.ref, safe='')}/audio"
            ) as r:
                if r.status_code >= 400:
                    raise BackendUnavailable(f"Open Notebook audio download failed: HTTP {r.status_code}")
                with tmp.open("wb") as fh:
                    for chunk in r.iter_bytes():
                        fh.write(chunk)
        except httpx.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            raise BackendUnavailable(f"Open Notebook audio download failed ({type(exc).__name__})") from None
        if tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            raise BackendUnavailable("Open Notebook returned an empty audio file")
        tmp.replace(path)
        return path

    def close(self) -> None:
        self._client.close()


def _need(data: dict[str, Any], key: str, what: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise BackendUnavailable(f"Open Notebook returned no {what} id")
    return value


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:300]
    return "generation failed"


def _detail(resp: httpx.Response) -> str:
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        return ""
    return f": {_first_line(str(detail))}" if detail else ""
