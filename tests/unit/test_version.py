"""The version has one source: ``version`` in pyproject.toml. ``pkpipe.__version__`` reads the installed
metadata, so a release that bumps one place and not the other fails here (the 0.1.4 tag shipped with a
stale ``__version__``)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pkpipe

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_dunder_version_agrees_with_pyproject():
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert pkpipe.__version__ == declared, (
        f"pyproject.toml says {declared}, pkpipe.__version__ says {pkpipe.__version__}: "
        "bump pyproject.toml only and run `uv sync`"
    )


def test_the_version_is_not_hard_coded_in_the_package():
    source = (PYPROJECT.parent / "src" / "pkpipe" / "__init__.py").read_text(encoding="utf-8")
    assert "importlib.metadata" in source
    assert not re.search(r"__version__\s*=\s*[\"']\d+\.\d+", source)
