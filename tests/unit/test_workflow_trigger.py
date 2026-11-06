"""The pipeline reads its as-of time from the checkpointed trigger output."""

from __future__ import annotations

import pytest

from src.orchestration.workflows import WorkflowError, run_at


def test_run_at_reads_the_trigger_output():
    """The trigger's ``at`` is the run's as-of time."""
    assert run_at({"trigger": "schedule", "at": "2026-11-04T12:00:00+00:00"}) == (
        "2026-11-04T12:00:00+00:00"
    )


def test_a_run_checkpointed_before_the_wiring_is_refused():
    """A trigger output without ``at`` predates the wired pipeline: say so, not KeyError."""
    with pytest.raises(WorkflowError, match="predates the wired pipeline; start a new run"):
        run_at({"trigger": "schedule", "dry_run": False})
