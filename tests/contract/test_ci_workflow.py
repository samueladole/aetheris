"""CI workflow tests — the properties whose absence kept main red.

Each class pins one root cause of a CI failure, so the failure cannot return
silently:

  1. **The npm lockfile reaches CI.** ``npm ci`` refuses to run without
     ``package-lock.json``; git-ignoring it broke the Node licence gate and the
     docs deploy.
  2. **Torch is installed CPU-only.** PyPI's Linux ``torch`` is the CUDA build,
     which pulls in NVIDIA's proprietary wheels. Aetheris is CPU-only, and
     Principle I forbids non-permissive runtime dependencies.
  3. **The docs job runs the canonical gate sequence.** A hand-copied list of
     gates drifted from ``aetheris docs build`` and dropped the built-HTML repair
     step, so the structural accessibility gate failed in CI but passed locally.
  4. **The verify job can run the whole suite.** Several contract tests build
     the documentation site and drive a browser, so the job needs the docs extra
     and Playwright's Chromium.

These parse the workflow files directly rather than assuming their content.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CI_WORKFLOW = WORKFLOWS / "ci.yml"

requires_yaml = pytest.mark.skipif(yaml is None, reason="pyyaml not installed")


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), f"{path.name} did not parse to a mapping"
    return data


def _run_commands(job: dict) -> list[str]:
    return [str(step["run"]) for step in job.get("steps", []) if "run" in step]


def _installs_project(command: str) -> bool:
    return "pip install" in command and ('-e "."' in command or '-e ".[' in command)


@pytest.fixture()
def ci() -> dict:
    """Parse the CI workflow, or fail loudly."""
    assert CI_WORKFLOW.is_file(), f"{CI_WORKFLOW} is missing"
    return _load(CI_WORKFLOW)


class TestLockfileReachesCi:
    """``npm ci`` needs a committed lockfile."""

    def test_package_lock_exists(self):
        """Test that the npm lockfile exists."""
        assert (REPO_ROOT / "package-lock.json").is_file()

    @pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
    def test_package_lock_is_not_git_ignored(self):
        """An ignored lockfile never reaches CI, so ``npm ci`` fails there."""
        proc = subprocess.run(
            ["git", "check-ignore", "-q", "package-lock.json"],
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=30,
        )
        if proc.returncode == 128:
            pytest.skip("not a git checkout")
        assert proc.returncode == 1, (
            "package-lock.json is git-ignored, so `npm ci` in CI has no lockfile"
        )


class TestTestsDoNotDependOnIgnoredFiles:
    """A file the suite needs must reach CI's clean checkout."""

    @pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
    def test_no_test_file_is_git_ignored(self):
        """An ignored fixture passes locally and breaks collection in CI.

        Recorded cassettes are the one exception: they hold provider data that
        the free-tier terms forbid redistributing, so they stay local and the
        tests that read them must not depend on their presence.
        """
        proc = subprocess.run(
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "tests/"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            pytest.skip("not a git checkout")
        ignored = [
            path
            for path in proc.stdout.splitlines()
            if "__pycache__" not in path
            and not (path.startswith("tests/fixtures/cassettes/") and path.endswith(".yaml"))
        ]
        assert not ignored, f"git-ignored files under tests/ never reach CI: {ignored}"


@requires_yaml
class TestCpuOnlyTorch:
    """Every project install in every workflow resolves CPU-only torch."""

    def test_every_project_install_uses_the_cpu_torch_backend(self):
        """Test that each project install passes --torch-backend cpu."""
        installs = [
            (path.name, command)
            for path in sorted(WORKFLOWS.glob("*.yml"))
            for job in _load(path).get("jobs", {}).values()
            for command in _run_commands(job)
            if _installs_project(command)
        ]
        assert installs, "no workflow installs the project"
        for name, command in installs:
            assert "--torch-backend cpu" in command, (
                f"{name}: {command!r} resolves PyPI's CUDA torch on Linux, which "
                "pulls in NVIDIA's proprietary wheels (Principle I)"
            )


@requires_yaml
class TestDocsJobRunsTheCanonicalGates:
    """The CI docs job runs ``aetheris docs build`` rather than a copied gate list."""

    def test_docs_job_runs_aetheris_docs_build(self, ci):
        """Test that the docs job delegates to aetheris docs build."""
        commands = _run_commands(ci["jobs"]["docs"])
        assert any("aetheris docs build" in c for c in commands), (
            "the docs job must run `aetheris docs build`, the single gate "
            "sequence; a hand-copied list drifted and dropped the HTML repair step"
        )

    def test_docs_job_installs_playwright_chromium(self, ci):
        """The rendered accessibility gate drives a real browser."""
        commands = _run_commands(ci["jobs"]["docs"])
        assert any("playwright install" in c and "chromium" in c for c in commands)


@requires_yaml
class TestVerifyJobCanRunTheSuite:
    """The verify job installs everything the test suite exercises."""

    def test_verify_job_installs_the_docs_extra(self, ci):
        """Contract tests build the documentation site, which needs mkdocs."""
        installs = [c for c in _run_commands(ci["jobs"]["verify"]) if _installs_project(c)]
        assert installs, "the verify job does not install the project"
        assert any("docs" in c for c in installs), (
            "the verify job runs tests that build the docs site but does not "
            "install the docs extra"
        )

    def test_verify_job_installs_playwright_chromium(self, ci):
        """The rendered accessibility gate tests drive a real browser."""
        commands = _run_commands(ci["jobs"]["verify"])
        assert any("playwright install" in c and "chromium" in c for c in commands)
