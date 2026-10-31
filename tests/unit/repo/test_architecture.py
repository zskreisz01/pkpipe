"""Spec 001 FR-001, FR-006 and constitution II: repository layout and the hexagonal import rule."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.conftest import REPO

SRC = REPO / "src" / "pkpipe"
CORE = ("engine", "domain", "steps")
CORE_THIRD_PARTY = {"langgraph", "pydantic", "langchain_core"}  # allowed besides pkpipe.ports and stdlib
STDLIB = __import__("sys").stdlib_module_names


def _package_of(path: Path) -> list[str]:
    """Dotted package of a module file below src/ (``src/pkpipe/engine/x.py`` -> ['pkpipe', 'engine'])."""
    try:
        return list(path.relative_to(SRC.parent).parent.parts)
    except ValueError:
        return ["pkpipe", "engine"]  # files outside src (checker self-test) are treated as core modules


def _imports(path: Path) -> set[str]:
    """Absolute module names imported by a file; relative imports are resolved against its package."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    mods.add(node.module)
                continue
            pkg = _package_of(path)
            base = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
            if node.module:
                mods.add(".".join([*base, node.module]))
            else:
                mods.update(".".join([*base, a.name]) for a in node.names)
    return mods


def _core_files() -> list[Path]:
    return [p for pkg in CORE for p in (SRC / pkg).rglob("*.py")]


@pytest.mark.parametrize("path", _core_files(), ids=lambda p: str(p.relative_to(SRC)))
def test_core_imports_only_ports_stdlib_and_allowed_libs(path):
    for mod in _imports(path):
        top = mod.split(".")[0]
        if top == "pkpipe":
            allowed = mod.startswith(("pkpipe.ports", "pkpipe.domain", "pkpipe.engine", "pkpipe.steps"))
            assert allowed or mod == "pkpipe", f"{path.name} imports {mod}: core may only use ports"
        else:
            assert top in STDLIB or top in CORE_THIRD_PARTY or top == "__future__", (
                f"{path.name} imports {mod}"
            )


def test_core_does_not_import_adapters():
    for path in _core_files():
        assert not any(m.startswith("pkpipe.adapters") for m in _imports(path)), path


def test_the_import_checker_would_catch_a_violation(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import httpx\nfrom pkpipe.adapters.x import y\n")
    assert {"httpx", "pkpipe.adapters.x"} <= _imports(bad)


def test_the_import_checker_resolves_relative_imports(tmp_path):
    """A core module must not reach adapters through a relative import (``from ..adapters import x``)."""
    bad = tmp_path / "bad.py"
    bad.write_text("from ..adapters.model import openai\nfrom .. import adapters\nfrom .registry import x\n")
    mods = _imports(bad)
    assert {"pkpipe.adapters.model", "pkpipe.adapters", "pkpipe.engine.registry"} <= mods


@pytest.mark.parametrize(
    "rel",
    [
        "src/pkpipe",
        "packs/learning-inbox",
        "mcp_servers",
        "evals",
        "docs",
        "website",
        "specs",
        "tests/unit",
        "tests/integration",
        "tests/e2e",
        "tests/fakes",
        ".github/workflows/ci.yml",
        ".github/workflows/pages.yml",
        ".python-version",
    ],
)
def test_repository_layout_matches_architecture_doc(rel):
    assert (REPO / rel).exists(), f"{rel} is part of the layout in docs/architecture.md"


def test_design_sources_and_exports_exist():
    """FR-006: BPMN and C4 draw.io sources, PNG exports for each page."""
    assert (REPO / "docs/design/pkpipe-process-model.drawio").is_file()
    assert (REPO / "docs/design/pkpipe-c4.drawio").is_file()
    assert len(list((REPO / "docs/img/bpmn").glob("*.png"))) >= 10
    assert len(list((REPO / "docs/img/c4").glob("*.png"))) >= 3


def test_every_process_page_references_an_existing_image_and_has_mermaid():
    """SC-002: each process page shows its image and its Mermaid diagram."""
    pages = sorted((REPO / "docs/process").glob("*.md"))
    assert pages
    for page in pages:
        text = page.read_text(encoding="utf-8")
        assert "```mermaid" in text, page.name
        images = [line for line in text.splitlines() if line.startswith("![")]
        assert images, page.name
        rel = images[0].split("(", 1)[1].rstrip(")")
        assert (page.parent / rel).resolve().is_file(), f"{page.name}: {rel}"


def test_python_version_is_pinned_to_312():
    assert (REPO / ".python-version").read_text().strip() == "3.12"
