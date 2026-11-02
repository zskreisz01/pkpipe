"""Spec 011, live: the context refresh with the real local model (LM Studio) on a synthetic vault of invented
project notes. The model's wording is not asserted; what code guarantees is: both packs exist within their
token budget, the enterprise pack names none of the names the notes declare, and the query guard's terms
file carries them."""

from __future__ import annotations

from pathlib import Path

import pytest

from pkpipe import app
from pkpipe.domain import context_pack as cp
from pkpipe.engine.registry import load_effective
from pkpipe.security import query_guard
from tests.fakes import FakeClock, FakeContextStore, FakeNotify, RecordingTracer
from tests.support.context_packs import CLIENT, PERSON, PROJECT, ROUTES, SECOND_CLIENT, seed_vault
from tests.support.flows import make_ports, make_service
from tests.support.pack_runtime import vault_tools


@pytest.mark.live
def test_live_refresh_writes_both_packs_and_the_enterprise_pack_names_no_declared_name(
    tmp_path: Path, pack_dir, lmstudio, monkeypatch
):
    monkeypatch.setenv("PKPIPE_MODEL_BASE_URL", lmstudio["base_url"])
    monkeypatch.setenv("PKPIPE_MODEL_NAME", lmstudio["model"])
    vault = tmp_path / "vault"
    seed_vault(vault)
    snap = load_effective(pack_dir, None).snapshot()
    store, notify = FakeContextStore(), FakeNotify(routes=ROUTES)
    ports = make_ports(
        model=app.make_model(snap),
        tools=vault_tools(vault),
        notify=notify,
        clock=FakeClock(),
        tracer=RecordingTracer(),
        context_store=store,
    )

    run = make_service(snap, ports).start("context_refresh", trigger="schedule")

    assert run.ok, run.error
    personal = cp.parse_pack(store.files["contexts/personal.md"]).body
    enterprise = cp.parse_pack(store.files["contexts/enterprise.md"]).body
    assert "Homelab" in personal or "homelab" in personal.lower()
    for body in (personal, enterprise):
        assert cp.estimate_tokens(body) <= 1500 and len(body.splitlines()) > 2
    declared = (CLIENT, PROJECT, PERSON, SECOND_CLIENT, "Falcon", "Brightwater", "Marlow")
    assert query_guard.check_arguments(enterprise, declared) is None, enterprise
    terms = query_guard.parse_terms(store.files["security/query_guard_terms.txt"])
    assert {CLIENT, PROJECT, SECOND_CLIENT} <= set(terms)
    assert notify.sent and not any(n in notify.sent[-1].message for n in (CLIENT, PROJECT, PERSON))
