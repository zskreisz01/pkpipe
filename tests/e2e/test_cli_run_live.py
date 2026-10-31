"""Spec 005 live e2e: ``pkpipe run triage`` as a subprocess with the real model (LM Studio) for the LLM step,
the real obsidian-vault MCP server and the real SQLite app DB. Nothing is faked."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from pkpipe.domain.triage import item_id_for
from pkpipe.obsidian import lists
from tests.conftest import PACK, REPO
from tests.support.pack_runtime import TRIAGE_FILE
from tests.support.vault_mcp import free_port, http_server, write_policy

UV = shutil.which("uv") or "uv"


@pytest.mark.live
def test_live_triage_run_categorizes_with_the_local_model(tmp_path, synthetic_vault, write_json, lmstudio):
    with http_server(write_policy(tmp_path, synthetic_vault), free_port()) as url:
        overlay = tmp_path / "overlay"
        write_json(overlay, "mcp_servers.json", {"obsidian": {"url": url}})
        env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PKPIPE_OVERLAY_DIR")}
        env.update({"PKPIPE_MODEL_BASE_URL": lmstudio["base_url"], "PKPIPE_MODEL_NAME": lmstudio["model"]})
        db = tmp_path / "app.db"
        proc = subprocess.run(
            [
                UV,
                "run",
                "--frozen",
                "pkpipe",
                "run",
                "triage",
                "--overlay",
                str(overlay),
                "--db",
                str(db),
                "--json",
            ],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
    assert proc.returncode == 0, proc.stderr[-3000:]
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["status"] == "done"
    categories = {c["id"] for c in json.loads((PACK / "categories.json").read_text())}
    per_item = {r["item"]["id"]: r for r in result["outputs"]["categorize"]}
    expected = {item_id_for("0-INBOX/example-tool.md"), item_id_for("0-INBOX/example-video.md")}
    assert set(per_item) == expected and all(r["status"] == "done" for r in per_item.values())
    for r in per_item.values():
        suggestion = r["output"]["outputs"]["suggest"]  # the real model's structured answer
        assert 0 <= suggestion["confidence"] <= 1 and suggestion["reason"].strip()
        assert r["item"]["category"] in categories
    rows = lists.read_triage(synthetic_vault / TRIAGE_FILE)["rows"]
    assert {r["item_id"] for r in rows} == expected
    assert all(r["state"] == "suggested" and r["ai_suggested_category"] in categories for r in rows)
