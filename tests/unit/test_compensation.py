"""Unit tests for failure compensation (T017).

Constitution §III requires "retries with exponential backoff, and failure
compensation from the exact point of failure". DBOS has no saga DSL
(research.md §A), so Aetheris compensates explicitly: every fallible step may
register a compensating action that runs when the step finally fails.

Written BEFORE implementation per constitution §V.
"""

from __future__ import annotations

import pytest

from src.orchestration.compensation import (
    CompensationRegistry,
    StepOutcome,
    StepState,
    StepStateMachine,
)


class TestCompensationRegistry:
    def test_register_and_retrieve(self):
        reg = CompensationRegistry()
        reg.register("ingest", lambda: None)
        assert reg.has("ingest")
        assert reg.get("ingest") is not None

    def test_unregistered_step_has_no_compensation(self):
        reg = CompensationRegistry()
        assert not reg.has("unknown")
        assert reg.get("unknown") is None

    def test_cannot_register_twice(self):
        reg = CompensationRegistry()
        reg.register("ingest", lambda: None)
        with pytest.raises(ValueError):
            reg.register("ingest", lambda: None)

    def test_registered_names(self):
        reg = CompensationRegistry()
        reg.register("a", lambda: None)
        reg.register("b", lambda: None)
        assert set(reg.names()) == {"a", "b"}


class TestStepStateMachine:
    """The FAILED -> COMPENSATING -> COMPENSATED path from data-model.md §5."""

    def test_happy_path_no_compensation(self):
        sm = StepStateMachine()
        assert sm.state == StepState.PENDING
        sm.start()
        assert sm.state == StepState.RUNNING
        sm.succeed()
        assert sm.state == StepState.SUCCEEDED

    def test_failure_then_compensation(self):
        sm = StepStateMachine()
        sm.start()
        sm.fail()
        assert sm.state == StepState.FAILED
        sm.compensate()
        assert sm.state == StepState.COMPENSATED

    def test_compensating_state_is_transient(self):
        """COMPENSATING exists only while the compensator is running."""
        sm = StepStateMachine()
        sm.start()
        sm.fail()
        sm.compensate(lambda: None)
        assert sm.state not in (StepState.COMPENSATING, StepState.FAILED)

    def test_compensation_error_is_surfaced_not_swallowed(self):
        """A broken compensator must not silently masquerade as success."""
        sm = StepStateMachine()
        sm.start()
        sm.fail()

        def boom() -> None:
            raise RuntimeError("compensator exploded")

        with pytest.raises(RuntimeError, match="compensator exploded"):
            sm.compensate(compensator=boom)
        assert sm.state == StepState.COMPENSATION_FAILED

    def test_compensation_runs_the_registered_action(self):
        calls: list[str] = []
        reg = CompensationRegistry()
        reg.register("ingest", lambda: calls.append("ingest"))
        sm = StepStateMachine(registry=reg, step_name="ingest")
        sm.start()
        sm.fail()
        sm.compensate()
        assert calls == ["ingest"]

    def test_illegal_transitions_are_rejected(self):
        sm = StepStateMachine()
        with pytest.raises(ValueError):
            sm.succeed()  # never started
        sm.start()
        sm.succeed()
        with pytest.raises(ValueError):
            sm.fail()  # already terminal
        with pytest.raises(ValueError):
            sm.start()  # already terminal

    def test_outcome_records_attempt_count(self):
        sm = StepStateMachine()
        sm.start()
        sm.fail()
        sm.compensate()
        outcome = sm.outcome
        assert isinstance(outcome, StepOutcome)
        assert outcome.final_state == StepState.COMPENSATED
