"""Gateway settings from the effective config (spec 012 FR-003, constitution III).

``mcp_servers.json`` entry ``learning`` selects the backend (``backend``) and tunes it (``options``); the URL
filter comes from ``security.json`` (``url_filter``). Everything has a default except what the owner must
choose, so ``backend: fake`` works without any option.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .backend import LearningBackend
from .backends import build_backend
from .errors import LearningError
from .gateway import DEFAULT_TIMEOUT_S, GatewayConfig, LearningGateway
from .store import JobStore
from .urlcheck import parse_deny_list

SERVER_NAME = "learning"
OPTION_KEYS = {"timeout_s", "data_dir", "audio_dir", "backend_options"}


@dataclass
class GatewaySettings:
    backend: str = "open_notebook"
    backend_options: dict[str, Any] = field(default_factory=dict)
    timeout_s: float = DEFAULT_TIMEOUT_S
    data_dir: Path = Path("~/.local/share/pkpipe/learning")
    audio_dir: Path | None = None
    block_private_networks: bool = True
    deny: list[str] = field(default_factory=list)

    @classmethod
    def from_config(
        cls, data: dict[str, Any], *, deny_text: str | None = None, environ=None
    ) -> GatewaySettings:
        """``data``: effective config (``mcp_servers``, ``security``); ``deny_text``: deny list file."""
        env = os.environ if environ is None else environ
        server = (data.get("mcp_servers") or {}).get(SERVER_NAME)
        if not server:
            raise LearningError(f"mcp_servers.json has no '{SERVER_NAME}' entry")
        options = dict(server.get("options") or {})
        unknown = sorted(set(options) - OPTION_KEYS)
        if unknown:
            raise LearningError(f"unknown learning option(s) {unknown} (known: {sorted(OPTION_KEYS)})")
        timeout = float(options.get("timeout_s", DEFAULT_TIMEOUT_S))
        if timeout <= 0:
            raise LearningError("timeout_s must be positive")
        url_filter = (data.get("security") or {}).get("url_filter") or {}
        base = Path(env.get("PKPIPE_DATA_DIR") or "~/.local/share/pkpipe") / "learning"
        return cls(
            backend=server.get("backend") or "open_notebook",
            backend_options=dict(options.get("backend_options") or {}),
            timeout_s=timeout,
            data_dir=Path(options.get("data_dir") or base).expanduser(),
            audio_dir=Path(options["audio_dir"]).expanduser() if options.get("audio_dir") else None,
            block_private_networks=bool(url_filter.get("block_private_networks", True)),
            deny=parse_deny_list(deny_text or ""),
        )

    def build(self, backend: LearningBackend | None = None, **kw) -> LearningGateway:
        backend = backend or build_backend(self.backend, self.backend_options)
        config = GatewayConfig(
            timeout_s=self.timeout_s,
            block_private_networks=self.block_private_networks,
            deny=self.deny,
            audio_dir=self.audio_dir or self.data_dir / "audio",
        )
        return LearningGateway(backend, JobStore(self.data_dir / "jobs.db"), config, **kw)
