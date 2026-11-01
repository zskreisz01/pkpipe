"""Free TCP ports for test servers, safe under pytest-xdist.

``bind(0)`` + close + hand the number to a subprocess is racy when several xdist workers start servers at the
same time: the OS may give the released port to another worker, whose readiness probe then connects to the
wrong server. Each worker therefore takes ports from its own block below the OS ephemeral range (macOS and
Linux start that at 32768 or higher), and never hands out the same port twice in one process.
"""

from __future__ import annotations

import os
import socket
import threading

BASE = 20000
BLOCK = 400  # ports per xdist worker: 30 workers fit below 32000
_lock = threading.Lock()
_handed: set[int] = set()


def worker_index() -> int:
    """0 for a plain run, N for xdist worker ``gwN``."""
    name = os.environ.get("PYTEST_XDIST_WORKER", "")
    return int(name[2:]) if name.startswith("gw") and name[2:].isdigit() else 0


def free_port() -> int:
    """A loopback port nobody listens on, from this worker's own block."""
    start = BASE + (worker_index() % 30) * BLOCK
    with _lock:
        for _ in range(2):
            for port in range(start, start + BLOCK):
                if port in _handed:
                    continue
                with socket.socket() as s:
                    try:
                        s.bind(("127.0.0.1", port))
                    except OSError:
                        continue
                _handed.add(port)
                return port
            _handed.clear()  # every port of the block was used once: start over (the old servers are gone)
    raise RuntimeError(f"no free port in {start}-{start + BLOCK - 1}")
