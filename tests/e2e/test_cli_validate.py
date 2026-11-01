"""Spec 001 FR-003 / US2 end to end: `uv run pkpipe validate` as a subprocess on pack + overlay."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from tests.conftest import REPO

UV = shutil.which("uv")
pytestmark = pytest.mark.skipif(UV is None, reason="uv not installed")

CATEGORY = {
    "id": "job",
    "label": "Jobs",
    "subflow": "other_resource",
    "para_path": "1-Projects/Jobs",
    "triage_hint": "Synthetic jobs.",
    "sessions": [],
}


def pkpipe(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PKPIPE_OVERLAY_DIR", "PKPIPE_PACK_DIR", "VIRTUAL_ENV")
    }
    env.update(env_extra or {})
    return subprocess.run(
        [UV, "run", "--frozen", "pkpipe", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_validate_public_pack_succeeds():
    r = pkpipe("validate")
    assert r.returncode == 0, r.stderr
    assert "config ok (version " in r.stdout


def test_validate_with_synthetic_overlay_from_env(overlay_dir, write_json):
    """SC-003: an overlay category is picked up through PKPIPE_OVERLAY_DIR with no code change."""
    write_json(overlay_dir, "categories.json", [CATEGORY, {"id": "marketing", "enabled": False}])
    base = json.loads(pkpipe("validate", "--json").stdout)
    r = pkpipe("validate", "--json", env_extra={"PKPIPE_OVERLAY_DIR": str(overlay_dir)})
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["ok"] and payload["version"] != base["version"], "overlay changes the effective version"


def test_validate_missing_overlay_dir_is_not_an_error(tmp_path):
    r = pkpipe("validate", "--json", env_extra={"PKPIPE_OVERLAY_DIR": str(tmp_path / "absent")})
    assert r.returncode == 0 and "does not exist" in r.stdout


def test_validate_fails_with_exit_code_1_and_names_the_file(overlay_dir, write_json):
    write_json(overlay_dir, "categories.json", [CATEGORY, CATEGORY])
    r = pkpipe("validate", "--overlay", str(overlay_dir))
    assert r.returncode == 1
    assert "categories.json" in r.stderr and "duplicate id 'job'" in r.stderr


def test_validate_rejects_secret_inlined_in_overlay_channel(overlay_dir, write_json):
    write_json(
        overlay_dir,
        "channels.json",
        {"channels": {"discord_main": {"type": "discord_webhook", "url": "https://u:secret@h/hook"}}},
    )
    r = pkpipe("validate", "--overlay", str(overlay_dir))
    assert r.returncode == 1 and "credentials" in r.stderr and "secret" not in r.stdout


def test_cli_lists_the_commands_and_stubs_fail_nonzero(tmp_path):
    help_out = pkpipe("--help").stdout
    for cmd in ("serve", "run", "runs", "eval", "validate"):
        assert cmd in help_out
    r = pkpipe("serve")
    assert r.returncode == 1 and "not implemented" in r.stderr
    r = pkpipe("eval", "--suite", "no_such_suite")  # spec 013 wired 'eval'
    assert r.returncode == 2 and "unknown suite" in r.stderr
    r = pkpipe("run", "no_such_flow", "--db", str(tmp_path / "app.db"))  # spec 005 wired 'run'
    assert r.returncode == 1 and "unknown flow 'no_such_flow'" in r.stderr


def test_validate_on_a_synthetic_vault_layout_does_not_touch_the_vault(synthetic_vault):
    """pkpipe validate only reads config; the vault stays byte-identical."""
    before = {p: p.read_bytes() for p in synthetic_vault.rglob("*") if p.is_file()}
    assert pkpipe("validate", env_extra={"PKPIPE_VAULT_DIR": str(synthetic_vault)}).returncode == 0
    assert before == {p: p.read_bytes() for p in synthetic_vault.rglob("*") if p.is_file()}


def test_validate_models_overlay_profiles_and_fallback_cycle(overlay_dir, write_json):
    """Spec 002 FR-003 / US2: new profile fields validate; a fallback cycle exits 1."""
    good = {
        "judge": {
            "base_url": "env:PKPIPE_MODEL_BASE_URL",
            "model": "m",
            "max_tokens": 512,
            "timeout_s": 30,
            "schema_retries": 2,
            "fallback": "default",
        },
    }
    write_json(overlay_dir, "models.json", good)
    assert pkpipe("validate", "--overlay", str(overlay_dir)).returncode == 0
    write_json(overlay_dir, "models.json", {"default": {"fallback": "judge"}, **good})
    r = pkpipe("validate", "--overlay", str(overlay_dir))
    assert r.returncode == 1 and "cycle" in r.stderr and "models" in r.stderr
