"""Unit tests for the bounded retry policy (T016) — FR-003.

Written BEFORE implementation per constitution §V (red-green-refactor).
Enforces the parameters rescued from spec §3.1 and the clarification Q5/A:
step-level checkpointing with bounded backoff (base 1s, x2, max 5 attempts,
jitter).
"""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.orchestration.retry import (
    BackoffPolicy,
    RetryBudgetExhausted,
    build_step_retry_policy,
)


def _settings(**overrides) -> Settings:
    base = {
        "retry_base_delay_ms": 1000,
        "retry_multiplier": 2.0,
        "retry_max_attempts": 5,
        "retry_jitter": True,
        "activity_timeout_s": 300.0,
    }
    base.update(overrides)
    return Settings(**base)


class TestBackoffPolicy:
    def test_exponential_sequence_without_jitter(self):
        """max_attempts=5 gives 4 retries: 1s, 2s, 4s, 8s."""
        policy = BackoffPolicy(base_delay_ms=1000, multiplier=2.0, max_attempts=5, jitter=False)
        # attempt 1 has no wait; the first retry waits the base delay.
        assert [policy.delay_before_attempt(n) for n in range(1, 6)] == [
            0,
            1000,
            2000,
            4000,
            8000,
        ]

    def test_first_retry_waits_the_base_delay(self):
        """Regression guard: the base delay must actually appear in the schedule.

        An earlier version computed base * multiplier^(attempt-1), yielding
        [0, 2000, 4000, ...] and silently skipping the configured 1000ms.
        """
        policy = BackoffPolicy(base_delay_ms=1000, multiplier=2.0, max_attempts=5, jitter=False)
        assert policy.delay_before_attempt(2) == 1000

    def test_jitter_stays_within_bounds(self):
        policy = BackoffPolicy(base_delay_ms=1000, multiplier=2.0, max_attempts=5, jitter=True)
        for attempt in range(1, 6):
            base = policy._base_for(attempt)
            delay = policy.delay_before_attempt(attempt)
            assert 0 <= delay <= base, "jitter must only ever shorten the delay"

    def test_attempts_are_one_indexed(self):
        policy = BackoffPolicy(base_delay_ms=1000, multiplier=2.0, max_attempts=5, jitter=False)
        with pytest.raises(ValueError):
            policy.delay_before_attempt(0)

    def test_no_wait_after_final_attempt(self):
        policy = BackoffPolicy(base_delay_ms=1000, multiplier=2.0, max_attempts=1, jitter=False)
        with pytest.raises(RetryBudgetExhausted):
            policy.delay_before_attempt(2)

    def test_max_delay_caps_the_schedule(self):
        policy = BackoffPolicy(
            base_delay_ms=1000, multiplier=2.0, max_attempts=6, jitter=False, max_delay_ms=2500
        )
        assert policy.delay_before_attempt(4) == 2500

    def test_rejects_invalid_configuration(self):
        with pytest.raises(ValueError):
            BackoffPolicy(base_delay_ms=0, multiplier=2.0, max_attempts=5)
        with pytest.raises(ValueError):
            BackoffPolicy(base_delay_ms=1000, multiplier=0.5, max_attempts=5)
        with pytest.raises(ValueError):
            BackoffPolicy(base_delay_ms=1000, multiplier=2.0, max_attempts=0)

    def test_is_exhausted(self):
        policy = BackoffPolicy(base_delay_ms=1000, multiplier=2.0, max_attempts=3)
        assert not policy.is_exhausted(2)
        assert policy.is_exhausted(3)


class TestSettingsToPolicy:
    def test_defaults_map_to_clarification_answer(self):
        policy = build_step_retry_policy(_settings())
        assert policy.base_delay_ms == 1000
        assert policy.multiplier == 2.0
        assert policy.max_attempts == 5
        assert policy.jitter is True

    def test_overrides_are_honoured(self):
        policy = build_step_retry_policy(
            _settings(
                retry_base_delay_ms=250,
                retry_multiplier=3.0,
                retry_max_attempts=2,
                retry_jitter=False,
            )
        )
        assert policy.base_delay_ms == 250
        assert policy.multiplier == 3.0
        assert policy.max_attempts == 2
        assert policy.jitter is False


class TestDBOSStepMapping:
    """The policy must translate into the kwargs DBOS's @step decorator accepts."""

    def test_maps_to_dbos_step_kwargs(self):
        from src.orchestration.retry import step_retry_kwargs

        kwargs = step_retry_kwargs(_settings())
        assert kwargs["retries_allowed"] is True
        assert kwargs["max_attempts"] == 5
        assert kwargs["interval_seconds"] == 1.0
        assert kwargs["backoff_rate"] == 2.0
        assert kwargs["timeout_seconds"] == 300.0

    def test_retries_disabled_when_no_jitter_disabled(self):
        from src.orchestration.retry import step_retry_kwargs

        kwargs = step_retry_kwargs(_settings(retry_max_attempts=1))
        assert kwargs["retries_allowed"] is False
        assert kwargs["max_attempts"] == 1
