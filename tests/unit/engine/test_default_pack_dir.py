"""The default pack is found in a source checkout and, when installed, in the wheel (pkpipe/_packs)."""

from pathlib import Path

from pkpipe.engine import registry


def test_env_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(registry.PACK_ENV, str(tmp_path))
    assert registry.default_pack_dir() == tmp_path


def test_checkout_layout_is_used_when_present(monkeypatch):
    monkeypatch.delenv(registry.PACK_ENV, raising=False)
    path = registry.default_pack_dir()
    assert path.name == "learning-inbox" and path.is_dir()


def test_installed_layout_is_used_when_there_is_no_checkout(monkeypatch, tmp_path):
    monkeypatch.delenv(registry.PACK_ENV, raising=False)
    pkg = tmp_path / "site" / "pkpipe" / "engine"
    pack = tmp_path / "site" / "pkpipe" / "_packs" / "learning-inbox"
    pack.mkdir(parents=True)
    pkg.mkdir(parents=True)
    monkeypatch.setattr(registry, "__file__", str(pkg / "registry.py"))
    assert registry.default_pack_dir() == Path(pack).resolve()
