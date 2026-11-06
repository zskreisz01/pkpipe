"""Spec 021 T012 / SC-002 (live): the probe-style description on synthetic images against LM Studio.

Twelve synthetic images (rendered by code from ``evals/datasets/images.jsonl``, nothing personal) go through
the same preparation, the same prompt and the same ``vision`` profile as a production run; at least eleven
must give a description that satisfies the ``image_description`` schema (the probe of 2026-11-05 on real
images gave 11 of 12). Skipped unless LM Studio serves the vision model; ``PKPIPE_REQUIRE_LIVE=1`` turns the
skip into a failure. Needs Pillow (optional extra ``images``). It is slow (a minute per image on a loaded
Mac): run it alone, ``PKPIPE_REQUIRE_LIVE=1 uv run pytest -m live tests/integration/test_images_live.py``
(tests/README.md).
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from pkpipe.adapters.image_pillow import PillowPreparer
from pkpipe.adapters.model_openai import OpenAIModel
from pkpipe.engine.registry import load_effective
from pkpipe.evals import synthetic_images as si
from pkpipe.evals.datasets import EvalPaths, load_jsonl
from pkpipe.evals.suites import case_canary, find_node, image_checks
from pkpipe.ports import ImageInput, ModelError
from pkpipe.security.canary import canary_notice
from pkpipe.steps.llm import build_prompt
from tests.conftest import REPO

pytest.importorskip("PIL", reason="the optional extra 'images' (Pillow) is not installed")
pytestmark = [pytest.mark.live("vision"), pytest.mark.timeout(3600)]

COUNT = 12
MIN_VALID = 11


def test_twelve_synthetic_images_give_at_least_eleven_valid_descriptions(pack_dir):
    snap = load_effective(pack_dir, None).snapshot()
    cases = load_jsonl(EvalPaths.create(REPO / "evals").find("datasets/images.jsonl"), "images")[:COUNT]
    from pkpipe.engine.config_models import ImagesConfig
    from pkpipe.evals.category_sessions import llm_config

    node = find_node(snap, "describe_image_item", "describe")
    cfg = llm_config(node)
    limits = ImagesConfig.model_validate(snap.data["images"])
    model = OpenAIModel(
        snap.data["models"], environ=os.environ
    )  # the pack's vision profile, as a run uses it
    system, user, schema = build_prompt(cfg, SimpleNamespace(snapshot=snap, inputs={}))
    valid, failures, injected = 0, [], []
    for case in cases:
        recipe = case.input["render"]
        prepared = PillowPreparer().prepare(
            si.render(recipe), max_side=limits.max_side, max_pixels=limits.max_pixels
        )
        canary = case_canary(case.id)
        try:
            reply = model.complete(
                system + canary_notice(canary),
                user,
                schema,
                profile=cfg.profile,
                images=[ImageInput(prepared.data, "image/jpeg", ref=si.render_ref(recipe))],
            )
        except ModelError as exc:
            failures.append(f"{case.id}: {type(exc).__name__}")
            continue
        valid += 1
        checks = image_checks(reply, case.expected or {}, canary)
        assert not checks["canary_leak"], case.id  # the canary of the system prompt never shows up
        if case.expected.get("injection"):
            injected.append((case.id, checks["injection_reported"]))
    assert valid >= MIN_VALID, f"only {valid} of {len(cases)} descriptions were valid: {failures}"
    assert injected, "the dataset has injection cases"
