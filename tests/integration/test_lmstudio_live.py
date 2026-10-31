"""Live check of the local model server this project targets (LM Studio, OpenAI-compatible API)."""

from __future__ import annotations

import json
import re
import urllib.request

import pytest

THINK = re.compile(r"<think>.*?</think>", re.DOTALL)

pytestmark = pytest.mark.live


def test_configured_model_is_loaded(lmstudio):
    with urllib.request.urlopen(f"{lmstudio['base_url']}/models", timeout=10) as resp:
        ids = [m["id"] for m in json.load(resp)["data"]]
    assert lmstudio["model"] in ids


def test_chat_completion_works_and_think_blocks_can_be_stripped(lmstudio):
    """Adapters must tolerate Qwen3.5 reasoning output (<think>...</think>) around the answer."""
    payload = {
        "model": lmstudio["model"],
        "messages": [{"role": "user", "content": "Reply with exactly one word: pong"}],
        "temperature": 0,
        "max_tokens": 1024,
    }
    req = urllib.request.Request(
        f"{lmstudio['base_url']}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        message = json.load(resp)["choices"][0]["message"]
    answer = THINK.sub("", message.get("content") or "").strip().lower()
    assert "pong" in answer
