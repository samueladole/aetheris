"""Step state machine and failure compensation (constitution §III).

Constitution §III requires recovery "with exponential backoff, and failure
compensation from the exact point of failure". DBOS supplies the durable
execution and retries but **has no saga DSL** (research.md §A), so Aetheris
registers a compensating action per fallible step and runs it explicitly when
that step is finally exhausted.

Compensation is deliberately *not* swallowed: a compensator that itself fails
moves the step to ``COMPENSATION_FAILED`` and raises, because silently
continuing after a failed rollback leaves the system in an unknown state.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum

from src.observability.metrics import get_metrics
from src.observability.telemetry import span_event


class StepState(StrEnum):
    """data-model.md §5 workflow-step lifecycle."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"


# Legal transitions: (from, to)
_TRANSITIONS: dict[StepState, set[StepState]] = {
    StepState.PENDING: {StepState.RUNNING},
    StepState.RUNNING: {StepState.SUCCEEDED, StepState.FAILED},
    StepState.FAILED: {StepState.COMPENSATING},
    StepState.COMPENSATING: {StepState.COMPENSATED, StepState.COMPENSATION_FAILED},
    StepState.SUCCEEDED: set(),
    StepState.COMPENSATED: set(),
    StepState.COMPENSATION_FAILED: set(),
}


@dataclass(frozen=True)
class StepOutcome:
    """Terminal result of one step's execution."""

    step_name: str
    final_state: StepState
    compensated: bool
    attempts: int

    @property
    def ok(self) -> bool:
        return self.final_state == StepState.SUCCEEDED

    @property
    def compensated_cleanly(self) -> bool:
        return self.final_state == StepState.COMPENSATED


class CompensationRegistry:
    """Maps a step name to the action that undoes it."""

    def __init__(self) -> None:
        self._actions: dict[str, Callable[[], None]] = {}

    def register(self, step_name: str, action: Callable[[], None]) -> None:
        """Attach a compensator to ``step_name``. Duplicate names are an error."""
        if step_name in self._actions:
            raise ValueError(f"step {step_name!r} already has a registered compensator")
        self._actions[step_name] = action

    def has(self, step_name: str) -> bool:
        return step_name in self._actions

    def get(self, step_name: str) -> Callable[[], None] | None:
        return self._actions.get(step_name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._actions))

    def run(self, step_name: str) -> None:
        """Run the registered compensator, or nothing if there is none."""
        action = self._actions.get(step_name)
        if action is not None:
            action()

    def __len__(self) -> int:
        return len(self._actions)

    def __iter__(self) -> Iterator[str]:
        return iter(self.names())


@dataclass
class StepStateMachine:
    """Tracks one step through its lifecycle, enforcing legal transitions."""

    step_name: str = "step"
    registry: CompensationRegistry = field(default_factory=CompensationRegistry)
    attempts: int = 0
    _state: StepState = StepState.PENDING

    # -- transitions -----------------------------------------------------
    @property
    def state(self) -> StepState:
        return self._state

    @property
    def terminal(self) -> bool:
        return self._state in {
            StepState.SUCCEEDED,
            StepState.COMPENSATED,
            StepState.COMPENSATION_FAILED,
        }

    def _to(self, new: StepState) -> None:
        if new not in _TRANSITIONS[self._state]:
            raise ValueError(
                f"illegal transition {self._state.value} -> {new.value} "
                f"for step {self.step_name!r}"
            )
        self._state = new

    def start(self) -> None:
        self._to(StepState.RUNNING)

    def succeed(self) -> None:
        self._to(StepState.SUCCEEDED)

    def fail(self) -> None:
        self._to(StepState.FAILED)
        get_metrics().step_failures.add(1)
        span_event("step.failed", **{"aetheris.step": self.step_name})

    def compensate(self, compensator: Callable[[], None] | None = None) -> StepState:
        """Run the compensator and move to COMPENSATED, or COMPENSATION_FAILED.

        A compensator registered in the registry is used unless one is passed
        explicitly (tests pass one directly for isolation).
        """
        self._to(StepState.COMPENSATING)
        action = compensator if compensator is not None else self.registry.get(self.step_name)
        try:
            if action is not None:
                action()
        except Exception:
            self._state = StepState.COMPENSATION_FAILED
            span_event("step.compensation_failed", **{"aetheris.step": self.step_name})
            raise
        self._state = StepState.COMPENSATED
        get_metrics().step_compensations.add(1)
        span_event("step.compensated", **{"aetheris.step": self.step_name})
        return self._state

    @property
    def outcome(self) -> StepOutcome:
        if self._state == StepState.RUNNING:
            return StepOutcome(self.step_name, StepState.RUNNING, False, self.attempts)
        return StepOutcome(
            self.step_name,
            self._state,
            self._state == StepState.COMPENSATED,
            self.attempts,
        )


__all__ = [
    "CompensationRegistry",
    "StepOutcome",
    "StepState",
    "StepStateMachine",
]
