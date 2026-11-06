"""A world for the image triage tests (spec 021): the pack's triage flow on a temp vault with fake ports, a
fake vision model scripted by image digest, a fake clock and the in-memory image store."""

from __future__ import annotations

import json
import time  # noqa: F401
from pathlib import Path

from pkpipe.engine.registry import load_effective
from pkpipe.obsidian import lists
from pkpipe.obsidian.policy import VaultPolicy
from tests.fakes import FakeClock, FakeImageStore, FakePreparer, FakeVisionModel, RecordingTracer
from tests.fakes.notify import FakeNotify
from tests.support.flows import make_ports, make_service
from tests.support.pack_runtime import (
    PACK_ROUTES,
    FakeLearning,
    fake_model_reply,
    synthetic_web,
    vault_tools,
)


class World:
    def __init__(
        self,
        vault: Path,
        pack_dir: Path,
        overlay: Path,
        *,
        images_config: dict | None = None,
        vision_script: dict | None = None,
        default_reply=None,
        clock: FakeClock | None = None,
        per_call_s: float = 0.0,
        store: FakeImageStore | None = None,
        tracer: RecordingTracer | None = None,
        snapshots=None,
    ):
        self.vault = vault
        self.clock = clock or FakeClock()
        self.store = store or FakeImageStore()
        self.tracer = tracer or RecordingTracer()
        if images_config is not None:
            (overlay / "images.json").write_text(json.dumps(images_config))
        self.snap = load_effective(pack_dir, overlay).snapshot()
        policy = VaultPolicy(vault_dir=vault)
        text = fake_model_reply(
            {"alpha": "learning-material", "local-first": "learning-material", "compare": "misc"}
        )
        self.model = FakeVisionModel(
            vision_script or {},
            default=default_reply,
            text=text,
            on_image_call=(lambda: self.clock.advance(per_call_s)) if per_call_s else None,
        )
        self.notify = FakeNotify(routes={**PACK_ROUTES, "images_estimate": ["discord_main"]})
        self.preparer = FakePreparer()
        learning = FakeLearning(polls_until_ready=0)
        self.ports = make_ports(
            tools=vault_tools(vault, learning, synthetic_web(), policy=policy),
            resolver=synthetic_web().resolve,
            model=self.model,
            notify=self.notify,
            tracer=self.tracer,
            clock=self.clock,
            image_store=self.store,
            image_preparer=self.preparer,
        )
        self.svc = make_service(
            lambda: self.snap, self.ports, snapshots_for=(lambda _s: snapshots) if snapshots else None
        )

    def run(self, flow: str = "triage"):
        run = self.svc.start(flow)
        assert run.ok, run.error
        return run

    @property
    def rows(self) -> dict[str, dict]:
        return {r["item_id"]: r for r in lists.read_triage(self.vault / "0-INBOX/Inbox-Triage.md")["rows"]}

    def listfile(self) -> str:
        return (self.vault / "0-INBOX/INBOX.md").read_text(encoding="utf-8")
