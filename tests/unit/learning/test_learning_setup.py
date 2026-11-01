"""Spec 012 T005: the Open Notebook setup script is idempotent and writes no secrets."""

from __future__ import annotations

import json

import httpx

from pkpipe.learning.open_notebook_setup import SetupOptions, run_setup


class FakeOpenNotebook:
    """Just enough of Open Notebook's configuration API to count what the script creates."""

    def __init__(self):
        self.store = {k: [] for k in ("credentials", "models", "speaker-profiles", "episode-profiles")}
        self.defaults = None
        self.writes = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")[1:]  # drop "api"
        body = json.loads(request.content) if request.content else None
        if request.method != "GET":
            self.writes.append((request.method, request.url.path, body))
        if parts == ["models", "defaults"] and request.method == "PUT":
            self.defaults = body
            return httpx.Response(200, json=body)
        coll = self.store.get(parts[0])
        if coll is None:
            return httpx.Response(404)
        if request.method == "GET":
            return httpx.Response(200, json=coll)
        if request.method == "POST":
            row = {"id": f"{parts[0]}:{len(coll) + 1}", **body}
            coll.append(row)
            return httpx.Response(200, json=row)
        row = next(r for r in coll if r["id"] == parts[1])
        row.update(body)
        return httpx.Response(200, json=row)


def test_setup_creates_everything_once_and_a_second_run_changes_nothing_but_profiles():
    fake = FakeOpenNotebook()
    opts = SetupOptions(speaches_host_url=None)
    ids = run_setup(opts, log=lambda m: None, transport=httpx.MockTransport(fake))
    assert [c["name"] for c in fake.store["credentials"]] == ["pkpipe-lmstudio", "pkpipe-tts"]
    names = {(m["name"], m["type"]) for m in fake.store["models"]}
    assert names == {
        ("qwen3.5-9b-mlx", "language"),
        ("google/gemma-4-12b", "language"),
        ("text-embedding-nomic-embed-text-v1.5", "embedding"),
        ("speaches-ai/Kokoro-82M-v1.0-ONNX", "text_to_speech"),
    }
    [episode] = fake.store["episode-profiles"]
    [speaker] = fake.store["speaker-profiles"]
    assert episode["name"] == "pkpipe_overview" and episode["num_segments"] == 3
    assert episode["outline_llm"] == ids["podcast_model"] != ids["chat_model"]
    assert [s["voice_id"] for s in speaker["speakers"]] == ["af_heart", "am_adam"]
    assert speaker["voice_model"] == ids["tts_model"] == fake.defaults["default_text_to_speech_model"]

    sizes = {k: len(v) for k, v in fake.store.items()}
    fake.writes.clear()
    run_setup(opts, log=lambda m: None, transport=httpx.MockTransport(fake))
    assert {k: len(v) for k, v in fake.store.items()} == sizes  # nothing duplicated
    assert all(m == "PUT" for m, *_ in fake.writes)  # only updates (profiles, defaults)


def test_no_secret_is_written():
    fake = FakeOpenNotebook()
    run_setup(SetupOptions(speaches_host_url=None), log=lambda m: None, transport=httpx.MockTransport(fake))
    keys = [c["api_key"] for c in fake.store["credentials"]]
    assert keys == ["not-needed", "not-needed"]  # a placeholder: LM Studio and Speaches ignore it
    urls = [c["base_url"] for c in fake.store["credentials"]]
    assert urls == ["http://host.docker.internal:1234/v1", "http://speaches:8000/v1"]
