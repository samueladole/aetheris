"""API reference generation tests (T018-T023) — FR-004, FR-005, FR-006, FR-008.

These verify the property that makes the reference sustainable: it regenerates
itself. A module added to the source tree produces a page with no manual edit,
a missing docstring fails the build before generation, and private names are
excluded by convention rather than by a maintained allowlist.

They run `aetheris docs build` against the real source tree, which is slow but
honest — the failure modes being tested (missing docstring, omitted module,
leaked private name) only manifest in a real build.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AETHERIS = REPO_ROOT / ".venv" / "bin" / "aetheris"
SRC = REPO_ROOT / "src"
SITE = REPO_ROOT / "site"

PROBE_PATH = SRC / "graph" / "tmp_probe.py"
PROBE_PAGE = "reference/graph/tmp_probe/index.html"

PROBE_BODY = '''"""A temporary probe module used only by the API reference tests."""


def probe_function(x: int) -> int:
    """Return the input unchanged.

    Args:
        x: A number.

    Returns:
        The same number.
    """
    return x
'''

UNDOCUMENTED_BODY = '''"""A temporary probe module with an undocumented public function."""


def undocumented(y: int) -> int:
    return y
'''


def _build() -> subprocess.CompletedProcess:
    """Run the full documentation build with every gate."""
    return subprocess.run(
        [str(AETHERIS), "docs", "build"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )


def _pages(site: Path) -> set[str]:
    return {p.relative_to(site).as_posix() for p in site.rglob("*.html")}


def _documented_modules(src: Path) -> list[str]:
    """Dotted module paths that the generator would document."""
    return [
        ".".join(p.relative_to(src).with_suffix("").parts)
        for p in sorted(src.rglob("*.py"))
        if "__pycache__" not in p.parts and p.name != "__init__.py"
    ]


def _digest(directory: Path) -> dict[str, str]:
    """Map of relative path to content hash for every file under a directory."""
    if not directory.is_dir():
        return {}
    return {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


@pytest.fixture()
def clean_probe():
    """Ensure the probe file never survives a test, whatever happens."""
    PROBE_PATH.unlink(missing_ok=True)
    yield
    PROBE_PATH.unlink(missing_ok=True)


@pytest.fixture()
def built_site():
    """Build once per test, then remove the output."""
    proc = _build()
    assert proc.returncode == 0, f"documentation build failed:\n{proc.stdout}\n{proc.stderr}"
    yield SITE
    shutil.rmtree(SITE, ignore_errors=True)


class TestNewModuleAppearsAutomatically:
    """T018 — FR-004, FR-005: no manual edit is needed to add a page."""

    def test_new_module_produces_a_page(self, built_site, clean_probe):
        """Test that new module produces a page."""
        before = _pages(SITE)

        PROBE_PATH.write_text(PROBE_BODY, encoding="utf-8")
        proc = _build()
        PROBE_PATH.unlink(missing_ok=True)

        assert proc.returncode == 0, f"build failed after adding a module:\n{proc.stdout}"
        new_pages = _pages(SITE) - before
        assert PROBE_PAGE in new_pages, (
            f"no page generated for the new module. New pages: {sorted(new_pages)}"
        )

        html = (SITE / PROBE_PAGE).read_text()
        assert "probe_function" in html, "signature missing from the generated page"
        assert "Return the input unchanged." in html, "docstring text not rendered"

    def test_navigation_picks_up_the_new_module(self, built_site, clean_probe):
        """Test that navigation picks up the new module."""
        nav_before = (SITE / "index.html").read_text()

        PROBE_PATH.write_text(PROBE_BODY, encoding="utf-8")
        proc = _build()
        PROBE_PATH.unlink(missing_ok=True)

        assert proc.returncode == 0
        assert "tmp_probe" in (SITE / "index.html").read_text(), (
            "navigation did not include the new module without a manual edit"
        )
        assert nav_before != (SITE / "index.html").read_text()


class TestMissingDocstringFailsTheBuild:
    """T019 — FR-006: the gate fails before generation, not silently after."""

    def test_undocumented_public_function_fails(self, clean_probe):
        """Test that undocumented public function fails."""
        PROBE_PATH.write_text(UNDOCUMENTED_BODY, encoding="utf-8")
        try:
            proc = _build()
        finally:
            PROBE_PATH.unlink(missing_ok=True)

        assert proc.returncode != 0, "an undocumented public function did not fail the build"
        output = proc.stdout + proc.stderr
        assert "docstring gate" in output, (
            f"the failure should come from the docstring gate:\n{output[:800]}"
        )
        assert "tmp_probe" in output, "the offending path must be named so it can be fixed"


class TestModuleCoverage:
    """T020 — FR-009: no layer can be silently omitted."""

    def test_every_source_module_has_a_page(self, built_site):
        """Test that every source module has a page."""
        missing = []
        for module in _documented_modules(SRC):
            page = SITE / "reference" / (module.replace(".", "/") + "/index.html")
            flat = SITE / "reference" / module.replace(".", "_") / "index.html"
            if not page.exists() and not flat.exists():
                missing.append(module)

        assert not missing, (
            f"{len(missing)} source module(s) have no reference page: "
            f"{sorted(missing)[:8]}"
        )

    def test_no_generated_page_is_orphaned(self, built_site):
        """Test that no generated page is orphaned."""
        documented = set(_documented_modules(SRC))
        pages = {
            p.relative_to(SITE / "reference").parent.as_posix().replace("/", ".")
            for p in (SITE / "reference").rglob("index.html")
        }
        pages.discard(".")
        assert not (pages - documented), (
            f"reference pages with no source module: {sorted(pages - documented)}"
        )


class TestPrivateNamesExcluded:
    """T021 — FR-008: exclusion is by convention, not an allowlist."""

    def test_private_names_are_not_rendered(self, built_site):
        """Test that private names are not rendered."""
        leaked = []
        for html in (SITE / "reference").rglob("*.html"):
            text = html.read_text()
            for name in ("tmp_probe", "_private", "__internal"):
                if f">{name}<" in text:
                    leaked.append((html.name, name))
        assert not leaked, f"private names leaked into the reference: {leaked}"


class TestDeterminism:
    """T022 — unchanged source produces byte-identical output."""

    def test_two_builds_are_byte_identical(self):
        """Test that two builds are byte identical."""
        shutil.rmtree(SITE, ignore_errors=True)
        assert _build().returncode == 0
        first = _digest(SITE / "reference")

        shutil.rmtree(SITE, ignore_errors=True)
        assert _build().returncode == 0
        second = _digest(SITE / "reference")

        assert first == second, (
            "two builds of unchanged source produced different output — "
            "the generated reference is not deterministic"
        )
        shutil.rmtree(SITE, ignore_errors=True)
