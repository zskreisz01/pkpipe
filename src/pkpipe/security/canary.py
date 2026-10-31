"""Per-run canary token: placed in a system instruction, must never appear in any output."""

from __future__ import annotations

import secrets


def new_canary(prefix: str = "CNY") -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


def leaked(canary: str, *outputs: str) -> bool:
    """True if the canary (or its hex part) shows up in any output, e.g. after a prompt-leak attack."""
    hex_part = canary.split("-", 1)[-1]
    return any(canary in out or hex_part in out for out in outputs)


def wrap_untrusted(text: str, nonce: str | None = None) -> tuple[str, str]:
    """Datamarking: wrap untrusted content in random delimiters the system prompt refers to."""
    nonce = nonce or secrets.token_hex(4)
    return f"<<DATA:{nonce}>>\n{text}\n<<END:{nonce}>>", nonce
