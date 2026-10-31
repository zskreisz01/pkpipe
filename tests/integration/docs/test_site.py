"""Spec 001 US3 / FR-004 / FR-005: the Docusaurus site and the workflows that build and deploy it."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from tests.conftest import REPO

WEBSITE = REPO / "website"
CONFIG = (WEBSITE / "docusaurus.config.ts").read_text(encoding="utf-8")


def test_site_config_follows_the_spec():
    assert "path: '../docs'" in CONFIG and "routeBasePath: '/'" in CONFIG
    assert "blog: false" in CONFIG
    assert "url: 'https://zskreisz01.github.io'" in CONFIG and "baseUrl: '/pkpipe/'" in CONFIG
    assert "@docusaurus/theme-mermaid" in CONFIG and "mermaid: true" in CONFIG
    assert "onBrokenLinks: 'throw'" in CONFIG, "broken links must fail the build"


def test_mermaid_theme_is_a_declared_dependency_with_the_same_version_as_core():
    deps = json.loads((WEBSITE / "package.json").read_text())["dependencies"]
    assert deps["@docusaurus/theme-mermaid"] == deps["@docusaurus/core"]


def test_build_artefacts_are_gitignored():
    ignored = (REPO / ".gitignore").read_text()
    assert "node_modules/" in ignored and "website/build/" in ignored and "website/.docusaurus/" in ignored


def test_pages_workflow_builds_with_node20_and_deploys_via_pages_actions():
    wf = (REPO / ".github/workflows/pages.yml").read_text()
    for needle in (
        "node-version: 20",
        "npm ci",
        "npm run build",
        "actions/upload-pages-artifact",
        "actions/deploy-pages",
        "website/build",
        "docs/**",
        "website/**",
    ):
        assert needle in wf, needle


def test_ci_workflow_gates_pull_requests_on_ruff_and_pytest():
    wf = (REPO / ".github/workflows/ci.yml").read_text()
    assert "pull_request" in wf and "uv run ruff check" in wf and "uv run pytest" in wf
    assert "astral-sh/setup-uv" in wf


def test_docs_index_links_resolve_to_existing_pages():
    """Every relative .md link in docs/index.md must exist (a broken one would fail the site build)."""
    text = (REPO / "docs/index.md").read_text()
    for target in re.findall(r"\]\(([^)#:]+\.md)\)", text):
        assert (REPO / "docs" / target).is_file(), target


def test_specs_page_is_part_of_the_docs():
    """T010: the docs link to the spec index."""
    assert "specs/README.md" in (REPO / "docs/specs.md").read_text()
    assert "specs.md" in (REPO / "docs/index.md").read_text()


@pytest.mark.docs
@pytest.mark.skipif(
    not (WEBSITE / "node_modules").is_dir() or not shutil.which("npm"),
    reason="website/node_modules missing: run `npm ci` in website/",
)
def test_site_builds_and_contains_process_pages_with_images():
    """SC-002 / US3.2 (build level): every process page is built with image and theme-mermaid diagram."""
    result = subprocess.run(["npm", "run", "build"], cwd=WEBSITE, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    build = WEBSITE / "build"
    bundle = "\n".join(p.read_text(encoding="utf-8") for p in (build / "assets" / "js").glob("*.js"))
    assert "docusaurus-mermaid-container" in bundle, "theme-mermaid renderer missing from the bundle"
    pages = sorted((REPO / "docs" / "process").glob("*.md"))
    assert len(pages) >= 10
    for md in pages:
        html = build / "process" / md.stem / "index.html"
        assert html.is_file(), f"{md.stem}: page not built"
        imgs = re.findall(r'<img[^>]*src=(?:"|)/pkpipe/(assets/images/[^" >]+\.png)', html.read_text())
        assert imgs and all((build / i).is_file() for i in imgs), f"{md.stem}: process image missing"
        first = md.read_text(encoding="utf-8").split("```mermaid\n", 1)[1].splitlines()[0]
        assert f'.mermaid,{{value:"{first}' in bundle, f"{md.stem}: Mermaid block not rendered by theme"
