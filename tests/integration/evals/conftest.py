"""Shared live work of the eval tests (tests/README.md "Running").

Several live eval tests send the same requests: the publish-gate test changes one session instruction, so its
other sessions, the security suite (triage injections, the secure-fetch Reader) and the "before" side of the
pairwise comparison are exactly the requests the sessions + security test already sent with the pack config.
``live_eval_cassettes`` is one cassette directory per pytest session used in ``auto`` mode: the first test
that sends a request gets the live answer and records it, a later test with the identical request (same
prompt, schema and profile) gets that recorded live answer back. Every changed request still goes to the
real model, and a test run on its own (``-k``) records everything itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def live_eval_cassettes(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("live-eval-cassettes")
