"""One-time, idempotent configuration of an Open Notebook stack for the learning gateway (spec 012 T005).

Run after ``docker compose up -d`` (see ``deploy/open-notebook/README.md``)::

    uv run python -m pkpipe.learning.open_notebook_setup

It uses only Open Notebook's REST API (and Speaches' model API for the voice model) and creates, when missing:
the LM Studio credential and the TTS credential, the language / embedding / text-to-speech models, the default
models, the speaker profile (two Kokoro voices) and the episode profile that the gateway generates with.
Nothing secret is written: LM Studio and Speaches need no key, the placeholder is not a credential.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx

PROVIDER = "openai_compatible"


@dataclass
class SetupOptions:
    api_url: str = "http://127.0.0.1:5055"
    #: Where *Open Notebook* (inside Docker) reaches LM Studio and Speaches.
    llm_url: str = "http://host.docker.internal:1234/v1"
    tts_url: str = "http://speaches:8000/v1"
    #: Where *this script* reaches Speaches, to download the voice model (None = skip the download).
    speaches_host_url: str | None = "http://127.0.0.1:8969"
    chat_model: str = "qwen3.5-9b-mlx"
    #: Writes the outline and the transcript as JSON: a model that does not think out loud (Qwen3.5 puts
    #: its reasoning in front of the JSON, which the podcast pipeline cannot parse).
    podcast_model: str = "google/gemma-4-12b"
    embedding_model: str = "text-embedding-nomic-embed-text-v1.5"
    tts_model: str = "speaches-ai/Kokoro-82M-v1.0-ONNX"
    voices: tuple[str, str] = ("af_heart", "am_adam")
    speaker_profile: str = "pkpipe_duo"
    episode_profile: str = "pkpipe_overview"
    segments: int = 3  # Open Notebook accepts 3 to 20
    max_tokens: int = 8192
    language: str = "en"
    api_key: str = "not-needed"  # LM Studio and Speaches ignore it; Open Notebook wants a non-empty value


class SetupError(RuntimeError):
    pass


class _Api:
    def __init__(self, base: str, timeout: float = 60.0, transport: httpx.BaseTransport | None = None):
        self.client = httpx.Client(base_url=base.rstrip("/"), timeout=timeout, transport=transport)

    def call(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = self.client.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise SetupError(f"{method} {path}: not reachable ({type(exc).__name__})") from None
        if r.status_code >= 400:
            detail = r.text[:200].replace("\n", " ")
            raise SetupError(f"{method} {path}: HTTP {r.status_code} {detail}")
        return r.json() if r.content else None


def wait_healthy(api_url: str, timeout_s: float = 120.0) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if httpx.get(f"{api_url.rstrip('/')}/health", timeout=3).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise SetupError(f"Open Notebook is not healthy at {api_url} (is the stack running?)")
        time.sleep(2)


def _ensure_voice_model(opts: SetupOptions, log) -> None:
    if not opts.speaches_host_url:
        return
    speaches = _Api(opts.speaches_host_url, timeout=900)
    have = {m["id"] for m in speaches.call("GET", "/v1/models").get("data", [])}
    if opts.tts_model in have:
        return
    log(f"downloading voice model {opts.tts_model} into Speaches (once, about 300 MB)")
    speaches.call("POST", f"/v1/models/{opts.tts_model}")


def _credential(api: _Api, name: str, modalities: list[str], base_url: str, key: str) -> dict[str, Any]:
    for c in api.call("GET", "/api/credentials"):
        if c["name"] == name:
            if c.get("base_url") != base_url:
                api.call("PUT", f"/api/credentials/{c['id']}", json={"base_url": base_url})
            return c
    body = {
        "name": name,
        "provider": PROVIDER,
        "modalities": modalities,
        "base_url": base_url,
        "api_key": key,
    }
    return api.call("POST", "/api/credentials", json=body)


def _model(api: _Api, name: str, kind: str, credential: str) -> str:
    for m in api.call("GET", "/api/models"):
        if m["name"] == name and m["type"] == kind and m["provider"] == PROVIDER:
            return m["id"]
    body = {"name": name, "provider": PROVIDER, "type": kind, "credential": credential}
    return api.call("POST", "/api/models", json=body)["id"]


def _upsert(api: _Api, collection: str, name: str, body: dict[str, Any]) -> dict[str, Any]:
    for row in api.call("GET", f"/api/{collection}"):
        if row["name"] == name:
            return api.call("PUT", f"/api/{collection}/{row['id']}", json=body)
    return api.call("POST", f"/api/{collection}", json=body)


def run_setup(
    opts: SetupOptions | None = None, *, log=print, transport: httpx.BaseTransport | None = None
) -> dict[str, str]:
    """Idempotent. Returns the ids it configured (model ids, profile names)."""
    opts = opts or SetupOptions()
    if transport is None:
        wait_healthy(opts.api_url)
    _ensure_voice_model(opts, log)
    api = _Api(opts.api_url, transport=transport)
    llm = _credential(api, "pkpipe-lmstudio", ["language", "embedding"], opts.llm_url, opts.api_key)
    tts = _credential(api, "pkpipe-tts", ["text_to_speech"], opts.tts_url, opts.api_key)
    chat = _model(api, opts.chat_model, "language", llm["id"])
    podcast = (
        chat
        if opts.podcast_model == opts.chat_model
        else _model(api, opts.podcast_model, "language", llm["id"])
    )
    embedding = _model(api, opts.embedding_model, "embedding", llm["id"])
    voice = _model(api, opts.tts_model, "text_to_speech", tts["id"])
    api.call(
        "PUT",
        "/api/models/defaults",
        json={
            "default_chat_model": chat,
            "default_transformation_model": chat,
            "large_context_model": chat,
            "default_tools_model": chat,
            "default_embedding_model": embedding,
            "default_text_to_speech_model": voice,
        },
    )
    host, expert = opts.voices
    speaker = _upsert(
        api,
        "speaker-profiles",
        opts.speaker_profile,
        {
            "name": opts.speaker_profile,
            "description": "pkpipe: two local Kokoro voices",
            "voice_model": voice,
            "speakers": [
                {
                    "name": "Alex",
                    "voice_id": host,
                    "backstory": "A curious host who asks the questions a learner would ask.",
                    "personality": "Warm, curious, concise.",
                },
                {
                    "name": "Sam",
                    "voice_id": expert,
                    "backstory": "An expert who explains the material clearly, with examples.",
                    "personality": "Calm, precise, friendly.",
                },
            ],
        },
    )
    _upsert(
        api,
        "episode-profiles",
        opts.episode_profile,
        {
            "name": opts.episode_profile,
            "description": "pkpipe: short learning overview",
            "speaker_config": speaker["id"],
            "outline_llm": podcast,
            "transcript_llm": podcast,
            "language": opts.language,
            "default_briefing": "Create a podcast overview of the sources in English.",
            "num_segments": opts.segments,
            "max_tokens": opts.max_tokens,
        },
    )
    log(f"ready: episode profile '{opts.episode_profile}', speaker profile '{opts.speaker_profile}'")
    return {
        "chat_model": chat,
        "podcast_model": podcast,
        "tts_model": voice,
        "episode_profile": opts.episode_profile,
        "speaker_profile": opts.speaker_profile,
    }


def main(argv: list[str] | None = None) -> int:
    d = SetupOptions()
    ap = argparse.ArgumentParser(prog="pkpipe-open-notebook-setup", description=__doc__.split("\n\n")[0])
    ap.add_argument("--api-url", default=d.api_url)
    ap.add_argument("--llm-url", default=d.llm_url, help="LM Studio as seen from inside Docker")
    ap.add_argument("--tts-url", default=d.tts_url, help="Speaches as seen from inside Docker")
    ap.add_argument(
        "--speaches-url", default=d.speaches_host_url, help="Speaches as seen from here ('' = skip download)"
    )
    ap.add_argument("--chat-model", default=d.chat_model)
    ap.add_argument("--podcast-model", default=d.podcast_model)
    ap.add_argument("--embedding-model", default=d.embedding_model)
    ap.add_argument("--tts-model", default=d.tts_model)
    args = ap.parse_args(argv)
    opts = SetupOptions(
        api_url=args.api_url,
        llm_url=args.llm_url,
        tts_url=args.tts_url,
        speaches_host_url=args.speaches_url or None,
        chat_model=args.chat_model,
        podcast_model=args.podcast_model,
        embedding_model=args.embedding_model,
        tts_model=args.tts_model,
    )
    try:
        run_setup(opts)
    except SetupError as exc:
        print(f"setup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
