"""Spec 012 FR-003, US2 (backend switch is configuration), constitution III."""

from __future__ import annotations

import json

import pytest

from pkpipe.engine.registry import load_effective
from pkpipe.learning.backends import FakeBackend, OpenNotebookBackend
from pkpipe.learning.config import GatewaySettings
from pkpipe.learning.errors import LearningError


def settings(overlay_servers=None, **kw):
    data = {
        "mcp_servers": {"learning": {"transport": "streamable_http", "url": "http://x", "backend": "fake"}},
        "security": {"url_filter": {"block_private_networks": True, "max_bytes": 1}},
    }
    if overlay_servers:
        data["mcp_servers"]["learning"].update(overlay_servers)
    return GatewaySettings.from_config(data, **kw)


def test_the_public_pack_selects_open_notebook_with_a_90_minute_timeout(pack_dir):
    cfg = load_effective(pack_dir, None)
    s = GatewaySettings.from_config(cfg.data, environ={"PKPIPE_DATA_DIR": "/data"})
    assert s.backend == "open_notebook" and s.timeout_s == 5400
    assert str(s.data_dir) == "/data/learning" and s.block_private_networks is True
    assert cfg.data["mcp_servers"]["learning"]["allow"] == ["create", "status", "link", "audio"]


def test_overlay_switches_the_backend_without_touching_flows(pack_dir, overlay_dir, write_json):
    """US2-AS1: ``backend: notebooklm_browser`` is an overlay setting; the flows are unchanged."""
    write_json(overlay_dir, "mcp_servers.json", {"learning": {"backend": "notebooklm_browser"}})
    cfg = load_effective(pack_dir, overlay_dir)
    assert GatewaySettings.from_config(cfg.data).backend == "notebooklm_browser"
    assert (
        cfg.data["flows"]["learning_material"]
        == load_effective(pack_dir, None).data["flows"]["learning_material"]
    )


def test_options_set_timeout_and_backend_options():
    s = settings({"options": {"timeout_s": 600, "backend_options": {"ready_after": 3}}})
    assert s.timeout_s == 600
    gw = s.build()
    assert isinstance(gw.backend, FakeBackend) and gw.backend.ready_after == 3 and gw.config.timeout_s == 600


def test_open_notebook_backend_options_reach_the_backend(tmp_path):
    s = settings(
        {
            "backend": "open_notebook",
            "options": {"backend_options": {"api_url": "http://127.0.0.1:1", "episode_profile": "p"}},
        },
        environ={"PKPIPE_DATA_DIR": str(tmp_path)},
    )
    be = s.build().backend
    assert (
        isinstance(be, OpenNotebookBackend)
        and be.api_url == "http://127.0.0.1:1"
        and be.episode_profile == "p"
    )


def test_deny_list_and_private_network_setting_come_from_security():
    s = settings(deny_text="evil.example\n# x\n")
    assert s.deny == ["evil.example"]
    data = {
        "mcp_servers": {"learning": {"transport": "stdio", "command": "x", "backend": "fake"}},
        "security": {"url_filter": {"block_private_networks": False, "max_bytes": 1}},
    }
    assert GatewaySettings.from_config(data).block_private_networks is False


@pytest.mark.parametrize(
    ("servers", "match"),
    [
        ({"options": {"timeoutS": 5}}, "unknown learning option"),
        ({"options": {"timeout_s": 0}}, "timeout_s must be positive"),
        ({"backend": "notebooklm"}, "unknown learning backend"),
    ],
)
def test_bad_settings_are_clear_errors(servers, match, tmp_path):
    with pytest.raises(LearningError, match=match):
        settings(servers, environ={"PKPIPE_DATA_DIR": str(tmp_path)}).build()


def test_missing_server_entry_is_an_error():
    with pytest.raises(LearningError, match="no 'learning' entry"):
        GatewaySettings.from_config({"mcp_servers": {}})


def test_the_gateway_rereads_the_overlay_deny_list(pack_dir, tmp_path, write_json):
    """Spec 009: the handler writes the overlay copy of the deny-list after the gateway started; the
    gateway's loader resolves the file again (overlay first), so the new domain applies at once."""
    from pkpipe.learning.server import gateway_from_config

    overlay = tmp_path / "overlay"
    write_json(
        overlay,
        "mcp_servers.json",
        {"learning": {"backend": "fake", "options": {"data_dir": str(tmp_path / "d")}}},
    )
    gw = gateway_from_config(pack_dir, overlay, environ={})
    assert "flagged.example" not in gw.config.deny_loader()
    deny_rel = json.loads((pack_dir / "security.json").read_text())["url_filter"]["deny_list"]
    pack_list = (pack_dir / deny_rel).read_text(encoding="utf-8")
    (overlay / deny_rel).parent.mkdir(parents=True, exist_ok=True)
    (overlay / deny_rel).write_text(pack_list + "flagged.example  # pkpipe: flagged 1, last 2026-11-01\n")
    assert "flagged.example" in gw.config.deny_loader()
