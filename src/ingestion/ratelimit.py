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

    def available(self) -> float:
        with self._lock:
            self._refill(time.monotonic())
            return self._tokens


class SharedRateLimiter:
    """One bucket per provider, shared across all worker threads."""

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def bucket_for(self, spec: ProviderSpec) -> TokenBucket:
        key = spec.budget_key()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                per_minute = spec.effective_minute_limit()
                bucket = TokenBucket(capacity=per_minute, refill_per_second=per_minute / 60.0)
                self._buckets[key] = bucket
            return bucket

    def acquire(self, spec: ProviderSpec, *, timeout_s: float = 30.0) -> AcquireResult:
        """Block until the provider's bucket grants one request."""
        return self.bucket_for(spec).acquire(1.0, timeout_s=timeout_s)

    def available(self, spec: ProviderSpec) -> float:
        return self.bucket_for(spec).available()

    def snapshot(self) -> dict[str, float]:
        """Current remaining capacity per provider (for `aetheris status`)."""
        with self._lock:
            return {k: b.available() for k, b in self._buckets.items()}
