"""A local HTTP server that speaks the subset of Open Notebook's REST API the learning gateway uses.

It checks the request shapes the real API requires (422 otherwise), so a contract test against it catches a
backend that sends the wrong payload. It is a real server on a real port (integration level), not a mock
of the unit under test.
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel

from tests.support.ports import free_port


class Notebook(BaseModel):
    name: str
    description: str = ""


class Source(BaseModel):
    type: str
    url: str | None = None
    notebooks: list[str] | None = None
    embed: bool = False
    async_processing: bool = False


class Generate(BaseModel):
    episode_profile: str
    speaker_profile: str
    episode_name: str
    notebook_id: str | None = None
    content: str | None = None
    briefing_suffix: str | None = None


class FakeOpenNotebook:
    """State and behaviour: ``ready_after`` polls until a job completes; ``fail_with`` makes jobs fail."""

    def __init__(self, ready_after: int = 2, fail_with: str | None = None, password: str | None = None):
        self.ready_after, self.fail_with, self.password = ready_after, fail_with, password
        self.notebooks: dict[str, dict[str, Any]] = {}
        self.sources: list[dict[str, Any]] = []
        self.jobs: dict[str, dict[str, Any]] = {}
        self.polls: dict[str, int] = {}
        self.audio = b"ID3\x04fake mp3 bytes"
        self.unreachable_urls: set[str] = set()
        self.app = self._build()

    def _auth(self, authorization: str | None) -> None:
        if self.password and authorization != f"Bearer {self.password}":
            raise HTTPException(401, "Invalid or missing password")

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.get("/health")
        def health():
            return {"status": "healthy"}

        @app.post("/api/notebooks")
        def create_notebook(body: Notebook, authorization: str | None = Header(None)):
            self._auth(authorization)
            nb_id = f"notebook:n{len(self.notebooks) + 1}"
            self.notebooks[nb_id] = body.model_dump()
            return {"id": nb_id, **body.model_dump(), "source_count": 0}

        @app.post("/api/sources/json")
        def create_source(body: Source, authorization: str | None = Header(None)):
            self._auth(authorization)
            if body.type != "link" or not body.url:
                raise HTTPException(422, "link sources need a url")
            if not body.notebooks or any(n not in self.notebooks for n in body.notebooks):
                raise HTTPException(404, "Notebook not found")
            if body.url in self.unreachable_urls:
                raise HTTPException(500, "Failed to fetch the page")
            self.sources.append(body.model_dump())
            return {"id": f"source:s{len(self.sources)}", "title": "page"}

        @app.post("/api/podcasts/generate")
        def generate(body: Generate, authorization: str | None = Header(None)):
            self._auth(authorization)
            if body.notebook_id not in self.notebooks:
                raise HTTPException(404, "Notebook not found")
            job_id = f"command:j{len(self.jobs) + 1}"
            self.jobs[job_id] = {"request": body.model_dump(), "episode": f"episode:e{len(self.jobs) + 1}"}
            return {"job_id": job_id, "status": "submitted", "message": "started"}

        @app.get("/api/podcasts/jobs/{job_id}")
        def job(job_id: str, authorization: str | None = Header(None)):
            self._auth(authorization)
            if job_id not in self.jobs:
                raise HTTPException(404, "Job not found")
            self.polls[job_id] = self.polls.get(job_id, 0) + 1
            if self.fail_with:
                return {"job_id": job_id, "status": "failed", "result": None, "error_message": self.fail_with}
            if self.polls[job_id] < self.ready_after:
                return {"job_id": job_id, "status": "running", "result": {}, "error_message": ""}
            result = {"episode_id": self.jobs[job_id]["episode"], "success": True}
            return {"job_id": job_id, "status": "completed", "result": result, "error_message": ""}

        @app.get("/api/podcasts/episodes/{episode_id}/audio")
        def audio(episode_id: str, authorization: str | None = Header(None)):
            self._auth(authorization)
            if episode_id not in {j["episode"] for j in self.jobs.values()}:
                raise HTTPException(404, "Episode not found")
            return Response(self.audio, media_type="audio/mpeg")

        return app


@contextlib.contextmanager
def serve(fake: FakeOpenNotebook):
    """Run the fake on a free loopback port in a thread; yields its base URL."""
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(fake.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        if time.time() > deadline or not thread.is_alive():
            raise RuntimeError("fake Open Notebook did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
