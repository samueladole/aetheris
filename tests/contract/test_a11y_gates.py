"""Fail-closed tests for the documentation accessibility gates (T010, T011).

Vocabulary used throughout:
  * *static gate*  — ``html-validate`` over the built HTML files.
  * *rendered gate* — ``scripts/check_a11y_rendered.py``, which drives a real
    browser to check computed contrast and keyboard focus.
  * *fail closed*  — a gate that cannot execute, times out, or errors reports
    FAILURE. The opposite (reporting success while broken) is *fail open*.

The fail-open regression guard is T011 and is the most important test in this
file: it is the direct defence against the behaviour that got a
permissively-licensed alternative rejected during research (research.md §C).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BAD_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "docs" / "a11y-bad.html"
RENDERED_CHECKER = REPO_ROOT / "scripts" / "check_a11y_rendered.py"
NODE = "npx"


def _run(
    argv: list[str], *, cwd: Path | None = None, timeout: int = 180
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, cwd=cwd or REPO_ROOT, capture_output=True, text=True, timeout=timeout
    )


def _htmlvalidate_available() -> bool:
    try:
        return _run([NODE, "html-validate", "--version"]).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _rendered_available() -> bool:
    return RENDERED_CHECKER.is_file()


requires_htmlvalidate = pytest.mark.skipif(
    not _htmlvalidate_available(), reason="html-validate not runnable via npx"
)
requires_rendered = pytest.mark.skipif(
    not _rendered_available(),
    reason="scripts/check_a11y_rendered.py not implemented yet (T012)",
)


class TestBadFixtureIsDeliberatelyBroken:
    """The fixture itself must stay broken, or these tests prove nothing."""

    def test_fixture_exists(self):
        """Test that fixture exists."""
        assert BAD_FIXTURE.is_file(), "the permanent a11y fixture is missing"

    def test_fixture_contains_the_declared_defects(self):
        """Test that fixture contains the declared defects."""
        body = BAD_FIXTURE.read_text()
        assert "<h1>" in body and "<h4>" in body, "fixture must skip a heading level"
        assert "<img src=" in body and "alt=" not in body.split("<img")[1].split(">")[0]
        assert 'aria-label="Search"' in body
        assert body.count('aria-label="Search"') >= 2, "fixture must duplicate a landmark"


@requires_htmlvalidate
class TestStaticGateFailsClosed:
    """T010: the static gate must reject the bad fixture."""

    def test_static_gate_rejects_the_bad_fixture(self):
        """Test that static gate rejects the bad fixture."""
        proc = _run([NODE, "html-validate", str(BAD_FIXTURE), "--formatter", "stylish"])
        assert proc.returncode != 0, (
            "html-validate accepted a page with known defects — the gate is broken.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )

    def test_static_gate_names_the_offending_rules(self):
        """Test that static gate names the offending rules."""
        proc = _run([NODE, "html-validate", str(BAD_FIXTURE), "--formatter", "stylish"])
        output = proc.stdout + proc.stderr
        # These two rules are enabled in .htmlvalidate.json and both fire here.
        assert "wcag/h37" in output, "the missing-alt defect must be reported"
        assert "unique-landmark" in output, "the duplicate landmark must be reported"

    def test_static_gate_reports_a_fixture_copy_in_an_arbitrary_location(self):
        """The gate judges content, not path — no special-casing the fixture."""
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "elsewhere.html"
            shutil.copy(BAD_FIXTURE, copy)
            proc = _run([NODE, "html-validate", str(copy)])
            assert proc.returncode != 0


@requires_rendered
class TestRenderedGateFailsClosed:
    """T010: the rendered gate (contrast, keyboard focus) must also reject it."""

    def test_rendered_gate_rejects_the_bad_fixture(self):
        """Test that rendered gate rejects the bad fixture."""
        proc = _run(
            [sys.executable, str(RENDERED_CHECKER), str(BAD_FIXTURE.parent)]
        )
        assert proc.returncode != 0, (
            "the rendered checker accepted a page with low contrast and skipped "
            "headings — the gate is broken.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )

    def test_rendered_gate_names_the_contrast_violation(self):
        """Test that rendered gate names the contrast violation."""
        proc = _run(
            [sys.executable, str(RENDERED_CHECKER), str(BAD_FIXTURE.parent)]
        )
        assert "contrast" in (proc.stdout + proc.stderr).lower()


class TestGateWrapperFailsClosed:
    """T011: a gate that cannot run must never report success.

    This is the fail-open regression guard. It exercises the same
    ``_run_gate`` helper the CLI uses, so it tests the behaviour that actually
    ships rather than a reimplementation of it.
    """

    def test_missing_command_is_a_failure(self):
        """Test that missing command is a failure."""
        from src.cli.main import _run_gate

        passed, detail = _run_gate(
            ["definitely-not-a-real-command-xyz"], name="missing command"
        )
        assert passed is False, "a gate whose binary is absent must fail, not pass"
        assert detail

    def test_missing_script_is_a_failure(self):
        """Test that missing script is a failure."""
        from src.cli.main import _run_gate

        passed, detail = _run_gate(
            [sys.executable, "scripts/does_not_exist.py"], name="missing script"
        )
        assert passed is False
        assert detail

    def test_nonzero_exit_is_a_failure(self):
        """Test that nonzero exit is a failure."""
        from src.cli.main import _run_gate

        passed, detail = _run_gate(
            [sys.executable, "-c", "import sys; sys.exit(3)"], name="nonzero exit"
        )
        assert passed is False
        assert "3" in detail

    def test_zero_exit_is_a_success(self):
        """Test that zero exit is a success."""
        from src.cli.main import _run_gate

        passed, detail = _run_gate(
            [sys.executable, "-c", "pass"], name="clean exit"
        )
        assert passed is True
        assert detail == ""

    def test_a_gate_that_hangs_is_a_failure(self):
        """A timed-out gate must fail, never silently pass."""
        import subprocess as sp

        import src.cli.main as cli

        original = cli.subprocess.run

        def fake_run(*args, **kwargs):
            raise sp.TimeoutExpired(cmd="sleep", timeout=kwargs.get("timeout", 1))

        cli.subprocess.run = fake_run
        try:
            passed, detail = cli._run_gate(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                name="hanging gate",
            )
        finally:
            cli.subprocess.run = original

        assert passed is False, "a timed-out gate must fail"
        assert "timed out" in detail

    def test_no_exception_escapes_from_a_broken_gate(self):
        """A gate must never crash the CLI — failure is reported, not raised."""
        from src.cli.main import _run_gate

        try:
            passed, detail = _run_gate(
                ["/nonexistent/path/to/nothing"], name="unrunnable"
            )
        except Exception as exc:  # noqa: BLE001 - this is the assertion
            pytest.fail(f"_run_gate raised instead of reporting failure: {exc}")
        assert passed is False
