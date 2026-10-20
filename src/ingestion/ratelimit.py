"""Thread-safe token-bucket rate limiter (FR-008).

Spec §3.2 requires a "shared token-bucket/sliding-window rate limiter across
worker threads". A bucket is shared per provider, so concurrent workers calling
the same provider collectively respect that provider's free-tier ceiling.

Design note (plan.md V2): the limiter is deliberately **in-process**. The spec
only requires cross-*thread* sharing, which in-process state satisfies, so no
external counter store (Redis/Valkey) is needed in v1.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from src.config.providers import ProviderSpec


@dataclass(frozen=True)
class AcquireResult:
    """Outcome of a single acquisition attempt."""

    granted: bool
    waited_s: float
    remaining: float
    limit: int


class TokenBucket:
    """A classic token bucket: capacity is the per-minute rate, refilled per second."""

    def __init__(self, *, capacity: float, refill_per_second: float) -> None:
        """Initialise the instance."""
        if capacity <= 0 or refill_per_second <= 0:
            raise ValueError("capacity and refill_per_second must be positive")
        self.capacity = float(capacity)
        self.refill_per_second = float(refill_per_second)
        self._tokens = float(capacity)
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_second)
            self._updated = now

    def try_acquire(self, cost: float = 1.0) -> AcquireResult:
        """Attempt to take ``cost`` tokens without ever blocking indefinitely."""
        with self._lock:
            self._refill(time.monotonic())
            if self._tokens >= cost:
                self._tokens -= cost
                return AcquireResult(True, 0.0, self._tokens, self.capacity)
            return AcquireResult(False, 0.0, self._tokens, self.capacity)

    def acquire(self, cost: float = 1.0, timeout_s: float | None = None) -> AcquireResult:
        """Block until ``cost`` tokens are available or ``timeout_s`` elapses.

        Raises :class:`TimeoutError` when the timeout expires while waiting, so a
        stalled provider cannot hang the whole pipeline.
        """
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            with self._lock:
                self._refill(time.monotonic())
                if self._tokens >= cost:
                    self._tokens -= cost
                    return AcquireResult(True, 0.0, self._tokens, self.capacity)
                deficit = cost - self._tokens
                wait_s = deficit / self.refill_per_second
            if deadline is not None and time.monotonic() + wait_s > deadline:
                raise TimeoutError(
                    f"Timed out waiting {timeout_s}s for {cost} tokens "
                    f"(limit {self.capacity}/s)"
                )
            time.sleep(min(wait_s, 0.5))

    def seconds_until(self, cost: float = 1.0) -> float:
        """Seconds until ``cost`` tokens are available (0 when they already are)."""
        with self._lock:
            self._refill(time.monotonic())
            deficit = cost - self._tokens
            return max(0.0, deficit / self.refill_per_second)

    def available(self) -> float:
        """Handle the available."""
        with self._lock:
            self._refill(time.monotonic())
            return self._tokens


def _windows(spec: ProviderSpec) -> tuple[tuple[int, float], ...]:
    """The ``(quota, window_seconds)`` pairs a provider declares, or 60/minute."""
    declared = (
        (spec.requests_per_minute, 60.0),
        (spec.requests_per_hour, 3600.0),
        (spec.requests_per_day, 86400.0),
    )
    windows = tuple((quota, seconds) for quota, seconds in declared if quota is not None)
    return windows or ((60, 60.0),)


class SharedRateLimiter:
    """One bucket per declared quota window per provider, shared across threads."""

    def __init__(self) -> None:
        """Initialise the instance."""
        self._buckets: dict[str, tuple[TokenBucket, ...]] = {}
        self._lock = threading.Lock()

    def buckets_for(self, spec: ProviderSpec) -> tuple[TokenBucket, ...]:
        """The provider's buckets: one per declared minute/hour/day quota."""
        key = spec.budget_key()
        with self._lock:
            buckets = self._buckets.get(key)
            if buckets is None:
                buckets = tuple(
                    TokenBucket(capacity=quota, refill_per_second=quota / seconds)
                    for quota, seconds in _windows(spec)
                )
                self._buckets[key] = buckets
            return buckets

    def acquire(self, spec: ProviderSpec, *, timeout_s: float = 30.0) -> AcquireResult:
        """Take one token from every window of the provider, or from none.

        Blocks until all windows can grant together; raises :class:`TimeoutError`
        after ``timeout_s``. The check and the take happen under one lock, so a
        token is never spent in one window while another is empty.
        """
        buckets = self.buckets_for(spec)
        deadline = time.monotonic() + timeout_s
        while True:
            with self._lock:
                wait_s = max(b.seconds_until(1.0) for b in buckets)
                if wait_s <= 0.0:
                    results = [b.try_acquire(1.0) for b in buckets]
                    tightest = min(results, key=lambda r: r.remaining)
                    return AcquireResult(True, 0.0, tightest.remaining, int(tightest.limit))
            if time.monotonic() + wait_s > deadline:
                raise TimeoutError(
                    f"Timed out waiting {timeout_s}s for a {spec.name} rate-limit slot"
                )
            time.sleep(min(wait_s, 0.5))

    def available(self, spec: ProviderSpec) -> float:
        """The fewest tokens left across the provider's windows."""
        return min(b.available() for b in self.buckets_for(spec))

    def snapshot(self) -> dict[str, float]:
        """Current remaining capacity per provider (for `aetheris status`)."""
        with self._lock:
            return {k: min(b.available() for b in bs) for k, bs in self._buckets.items()}
