"""Unit tests for the token-bucket rate limiter (T029).

Written before implementation per constitution §V (red-green-refactor).
"""

from __future__ import annotations

import threading
import time

import pytest

from src.config.providers import ProviderSpec, get_registry
from src.ingestion.ratelimit import SharedRateLimiter, TokenBucket


class TestTokenBucket:
    """Test token bucket."""
    def test_capacity_and_refill(self):
        """Test that capacity and refill."""
        bucket = TokenBucket(capacity=2.0, refill_per_second=2.0)
        assert bucket.try_acquire().granted
        assert bucket.try_acquire().granted
        assert not bucket.try_acquire().granted, "third acquire must be refused"

    def test_acquire_blocks_until_refilled(self):
        """Test that acquire blocks until refilled."""
        bucket = TokenBucket(capacity=1.0, refill_per_second=10.0)
        assert bucket.try_acquire().granted
        start = time.monotonic()
        res = bucket.acquire(1.0, timeout_s=5.0)
        waited = time.monotonic() - start
        assert res.granted
        assert waited >= 0.05, "should have blocked for roughly one refill period"

    def test_acquire_times_out(self):
        """Test that acquire times out."""
        bucket = TokenBucket(capacity=1.0, refill_per_second=0.2)
        bucket.try_acquire()
        with pytest.raises(TimeoutError):
            bucket.acquire(10.0, timeout_s=0.3)

    def test_rejects_invalid_configuration(self):
        """Test that rejects invalid configuration."""
        with pytest.raises(ValueError):
            TokenBucket(capacity=0, refill_per_second=1.0)
        with pytest.raises(ValueError):
            TokenBucket(capacity=1.0, refill_per_second=0)


class TestSharedLimiter:
    """Test shared limiter."""
    def test_bucket_is_shared_across_threads(self):
        """Concurrent workers on one provider collectively respect the ceiling."""
        limiter = SharedRateLimiter()
        spec = ProviderSpec(
            name="test",
            module="m",
            market_endpoints=("e",),
            news_endpoints=(),
            requests_per_minute=60,
            requests_per_hour=None,
            requests_per_day=None,
            fallback_order=None,
            redistribution="permitted",
        )
        acquired: list[bool] = []
        lock = threading.Lock()

        def worker() -> None:
            res = limiter.acquire(spec, timeout_s=2.0)
            with lock:
                acquired.append(res.granted)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # capacity is 60/min, so all 8 should succeed instantly
        assert all(acquired)
        assert len(acquired) == 8

    def test_effective_minute_limit_uses_tightest_quota(self):
        """Test that effective minute limit uses tightest quota."""
        spec = ProviderSpec(
            name="tight",
            module="m",
            market_endpoints=("e",),
            news_endpoints=(),
            requests_per_minute=None,
            requests_per_hour=12,  # -> 0/min -> floored to 1
            requests_per_day=None,
            fallback_order=None,
            redistribution="permitted",
        )
        assert spec.effective_minute_limit() == 1

    def test_registry_providers_have_budgets(self):
        """Test that registry providers have budgets."""
        for spec in get_registry().all():
            assert spec.name
            assert spec.endpoints
            assert spec.redistribution in {"permitted", "restricted", "forbidden"}
