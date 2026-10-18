"""README sync check tests — the gate that keeps README.md's claims true.

The check is only useful if it fails on the drift that actually happens. These
tests pin three failure modes that previously slipped through: a stale
pipeline-progress row, a "not yet implemented" claim about a task already
marked done, and a test count read from a suite that was not green.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "check_readme_sync", REPO_ROOT / "scripts" / "check_readme_sync.py"
)
assert _spec and _spec.loader
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

TASKS = """\
## Phase 1

- [X] T054 [P] [US4] Write failing point-in-time replay test
- [X] T064 [US4] Wire the query subcommands
- [ ] T070 [US5] Something still open
"""


def _named_check(name: str) -> dict[str, object]:
    matches = [check for check in sync.CHECKS if check["name"] == name]
    assert matches, f"no check named {name!r}"
    return matches[0]


class TestTaskProgress:
    """Status-table progress rows are recomputed from the task file."""

    def test_counts_checked_and_total(self, tmp_path):
        """Test that checked and total tasks are counted."""
        path = tmp_path / "tasks.md"
        path.write_text(TASKS)
        assert sync._task_progress(path) == (2, 3)

    def test_stale_pipeline_row_fails(self):
        """A pipeline row that disagrees with tasks.md is reported."""
        check = _named_check("pipeline task progress")
        failures = sync._check_facts("| Pipeline (`src/`) | 0 / 0 | layers |", [check])
        assert len(failures) == 1
        assert "the repository says" in failures[0]

    def test_current_pipeline_row_passes(self):
        """A pipeline row that matches tasks.md passes."""
        checked, total = sync._task_progress(sync.PIPELINE_TASKS)
        check = _named_check("pipeline task progress")
        readme = f"| Pipeline (`src/`) | {checked} / {total} | layers |"
        assert sync._check_facts(readme, [check]) == []


class TestPendingClaims:
    """A "not yet implemented" claim must cite tasks that are still open."""

    def test_claim_about_a_done_task_fails(self):
        """Test that a pending claim citing a checked task fails."""
        readme = "The `query` command is specified but not yet implemented (task T064)."
        failures = sync._stale_pending_claims(readme, TASKS)
        assert len(failures) == 1
        assert "T064" in failures[0]

    def test_claim_about_an_open_task_passes(self):
        """Test that a pending claim citing an open task passes."""
        readme = "Drift alerts are not yet implemented (task T070)."
        assert sync._stale_pending_claims(readme, TASKS) == []

    def test_task_cited_in_a_later_sentence_counts(self):
        """The task id may sit in a different sentence of the same paragraph."""
        readme = (
            "This rule is **specified but not yet implemented**. The alignment\n"
            "layer is task T054. Until it lands, review enforces it."
        )
        failures = sync._stale_pending_claims(readme, TASKS)
        assert len(failures) == 1
        assert "T054" in failures[0]

    def test_paragraphs_are_independent(self):
        """Test that a done task in another paragraph is not attributed."""
        readme = "Drift is not yet implemented (T070).\n\nT064 shipped the query grammar."
        assert sync._stale_pending_claims(readme, TASKS) == []

    def test_list_items_are_independent(self):
        """Test that a done task in a sibling bullet is not attributed."""
        readme = "- Drift is not yet implemented (T070).\n- T064 shipped the query grammar."
        assert sync._stale_pending_claims(readme, TASKS) == []

    def test_list_item_continuation_lines_belong_to_the_item(self):
        """Test that a wrapped bullet is read as one claim."""
        readme = "- The query grammar is not yet\n  implemented (T064).\n- Other."
        failures = sync._stale_pending_claims(readme, TASKS)
        assert len(failures) == 1
        assert "T064" in failures[0]

    def test_claim_without_a_task_fails(self):
        """An uncited pending claim cannot be verified, so it fails."""
        readme = "Streaming ingestion is not yet implemented."
        failures = sync._stale_pending_claims(readme, TASKS)
        assert len(failures) == 1
        assert "cite" in failures[0]

    def test_unknown_task_fails(self):
        """Test that a pending claim citing a nonexistent task fails."""
        readme = "Webhooks are not yet implemented (T999)."
        failures = sync._stale_pending_claims(readme, TASKS)
        assert len(failures) == 1
        assert "T999" in failures[0]


class TestTestCount:
    """The test count is the collected count, which no environment changes.

    A passed count varies with the machine: missing Node fails the docs tests,
    and absent provider credentials skip the cassette leak checks in CI.
    Whether the suite passes is the Tests step's job, not this check's.
    """

    def test_reads_the_collected_count(self):
        """Test that the collected count is read from collect-only output."""
        assert sync._parse_collected_count("a.py::t\n\n440 tests collected in 0.22s") == 440

    def test_reads_a_singular_count(self):
        """Test that a one-test summary is read."""
        assert sync._parse_collected_count("1 test collected in 0.01s") == 1

    def test_refuses_collection_errors(self):
        """A module that fails to import hides its tests from the count."""
        with pytest.raises(sync.SyncError, match="2 errors"):
            sync._parse_collected_count("438 tests collected, 2 errors in 0.50s")


class TestCheckFacts:
    """A fact that cannot be recomputed is reported, not raised."""

    def test_unverifiable_fact_is_reported(self):
        """Test that a SyncError from a check becomes a failure line."""

        def unverifiable() -> int:
            raise sync.SyncError("suite is not green")

        check = {
            "name": "test count",
            "pattern": r"\*\*(\d+) tests? pass\*\*",
            "actual": unverifiable,
            "hint": "Fix the suite.",
        }
        failures = sync._check_facts("**418 tests pass**", [check])
        assert len(failures) == 1
        assert "suite is not green" in failures[0]
