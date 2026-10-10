"""Bounded exponential backoff policy (FR-003).

Spec §3.1 requires "exponential backoff and activity timeouts for unreliable
external network boundaries". The exact parameters were fixed by clarification
Q5/A: step-level checkpointing with bounded backoff — base 1s, x2, max 5
attempts, jitter.

This module translates :class:`Settings` into (a) a pure backoff schedule that is
unit-testable without a workflow engine, and (b) the kwargs DBOS's ``@step``
decorator accepts, so the engine's own retry machinery is driven by the same
configured parameters rather than a second, competing policy.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from src.config.settings import Settings

# Defaults are the clarification Q5/A answer; they are also Settings' defaults.
DEFAULT_BASE_DELAY_MS = 1000
DEFAULT_MULTIPLIER = 2.0
DEFAULT_MAX_ATTEMPTS = 5


class RetryBudgetExhausted(RuntimeError):
    """Raised when a delay is requested beyond the configured attempt budget."""


@dataclass(frozen=True)
class BackoffPolicy:
    """A pure exponential backoff schedule with optional jitter.

    ``delay_before_attempt(n)`` is the wait *before* attempt ``n`` (1-indexed).
    Attempt 1 has no wait, so the first retry waits the base delay.

    ``activity_timeout_s`` is the per-step ceiling from FR-003 — it also bounds
    recovery time, which is what keeps SC-002 (resume < 60s) achievable.
    """

    base_delay_ms: int = DEFAULT_BASE_DELAY_MS
    multiplier: float = DEFAULT_MULTIPLIER
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    jitter: bool = True
    max_delay_ms: int | None = None
    activity_timeout_s: float | None = None

    def __post_init__(self) -> None:
        """Implement the __post_init__ special method."""
        if self.base_delay_ms <= 0:
            raise ValueError("base_delay_ms must be positive")
        if self.multiplier < 1.0:
            raise ValueError("multiplier must be >= 1.0")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")

    def _retry_delay(self, retry: int) -> int:
        """Un-jittered delay in ms before retry ``retry`` (1-indexed).

        ``retry`` counts *retries*, not attempts: retry 1 is the wait before
        attempt 2, so the base delay of 1000ms is what the first retry waits —
        not 2000ms.
        """
        if retry < 1:
            raise ValueError("retry is 1-indexed")
        delay = self.base_delay_ms * (self.multiplier ** (retry - 1))
        if self.max_delay_ms is not None:
            delay = min(delay, self.max_delay_ms)
        return int(delay)

    def _base_for(self, attempt: int) -> int:
        """Un-jittered delay before ``attempt``; 0 for the first attempt."""
        if attempt < 1:
            raise ValueError("attempt is 1-indexed")
        if attempt > self.max_attempts:
            raise RetryBudgetExhausted(
                f"attempt {attempt} exceeds budget of {self.max_attempts}"
            )
        if attempt == 1:
            return 0
        return self._retry_delay(attempt - 1)

    def delay_before_attempt(self, attempt: int) -> int:
        """Delay in milliseconds before ``attempt``; 0 for the first attempt."""
        base = self._base_for(attempt)
        if not self.jitter:
            return base
        # Jitter may only *shorten* the wait, never extend it: extending would
        # risk breaching a provider's rate window.
        return random.randint(0, base)  # noqa: S311 - jitter, not security

    def is_exhausted(self, attempts_made: int) -> bool:
        """Return whether this is exhausted."""
        return attempts_made >= self.max_attempts

    def as_dbos_kwargs(self) -> dict[str, object]:
        """Map onto the kwargs supported by DBOS 3.x ``@DBOS.step(...)``."""
        return step_retry_kwargs_from_policy(self)


def build_step_retry_policy(settings: Settings) -> BackoffPolicy:
    """Build the policy from runtime configuration (Settings)."""
    return BackoffPolicy(
        base_delay_ms=settings.retry_base_delay_ms,
        multiplier=settings.retry_multiplier,
        max_attempts=settings.retry_max_attempts,
        jitter=settings.retry_jitter,
        activity_timeout_s=settings.activity_timeout_s,
    )


def step_retry_kwargs(settings: Settings) -> dict[str, object]:
    """DBOS ``@step`` kwargs for the configured policy."""
    return step_retry_kwargs_from_policy(build_step_retry_policy(settings))


def step_retry_kwargs_from_policy(policy: BackoffPolicy) -> dict[str, object]:
    """Translate a :class:`BackoffPolicy` into DBOS step decorator kwargs."""
    # DBOS re-delivers the whole attempt sequence itself; when only one attempt
    # is configured, retries are disabled so the engine does not re-run a step
    # that the policy already considers terminal.
    retries_allowed = policy.max_attempts > 1
    kwargs: dict[str, object] = {
        "retries_allowed": retries_allowed,
        "max_attempts": policy.max_attempts,
        # DBOS expresses the base interval in seconds.
        "interval_seconds": policy.base_delay_ms / 1000.0,
        "backoff_rate": policy.multiplier,
    }
    if policy.activity_timeout_s is not None:
        kwargs["timeout_seconds"] = policy.activity_timeout_s
    return kwargs


__all__ = [
    "BackoffPolicy",
    "RetryBudgetExhausted",
    "DEFAULT_BASE_DELAY_MS",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MULTIPLIER",
    "build_step_retry_policy",
    "step_retry_kwargs",
    "step_retry_kwargs_from_policy",
]
