"""Unit tests for the TTL cache (T028) — FR-007.

Spec §3.2 requires "configurable TTLs to prevent hitting strict free-tier quotas"
and spec §4 requires the cache to be checked *before* a provider call. SC-003
(≥ 80% hit ratio at steady state) is computed from the counters asserted here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.ingestion.cache import TTLCache
from src.ingestion.payloads import RawPayloadStore


@pytest.fixture()
def store(tmp_path):
    """Test that store."""
    s = RawPayloadStore(str(tmp_path / "raw.db"))
    yield s
    s.close()


def _stored(store: RawPayloadStore, *, request_hash: str, provider: str = "fred",
            endpoint: str = "series/observations", body: bytes = b"x",
            ttl_seconds: int = 3600, age_hours: float = 0.0) -> None:
    fetched_at = datetime.now(UTC) - timedelta(hours=age_hours)
    store.store(
        provider=provider,
        endpoint=endpoint,
        request_hash=request_hash,
        body=body,
        fetched_at=fetched_at,
        ttl_seconds=ttl_seconds,
    )


class TestCacheHitAndMiss:
    """Test cache hit and miss."""
    def test_fresh_payload_is_a_hit(self, store):
        """Test that fresh payload is a hit."""
        _stored(store, request_hash="h")
        cache = TTLCache(store)
        outcome = cache.get("fred", "series/observations", "h")
        assert outcome.hit
        assert outcome.reason == "fresh"
        assert outcome.payload is not None

    def test_absent_is_a_miss(self, store):
        """Test that absent is a miss."""
        cache = TTLCache(store)
        outcome = cache.get("fred", "series/observations", "absent")
        assert not outcome.hit
        assert outcome.reason == "absent"

    def test_expired_is_a_miss(self, store):
        """Test that expired is a miss."""
        _stored(store, request_hash="h", age_hours=2, ttl_seconds=60)
        cache = TTLCache(store)
        outcome = cache.get("fred", "series/observations", "h")
        assert not outcome.hit
        assert outcome.reason == "stale"



class TestForceBypass:
    """A `--force` fetch must bypass the cache and re-read from the provider.

    The bypass is expressed as a TTL override of 0 passed to ``get()``, so the
    registry needs no separate "force" branch - its ``force`` flag maps
    directly onto the override.
    """

    def test_zero_override_makes_a_fresh_payload_stale(self, store):
        """Test that zero override makes a fresh payload stale."""
        _stored(store, request_hash="h", ttl_seconds=3600)
        cache = TTLCache(store)
        # Without an override this is a hit...
        assert cache.get("fred", "series/observations", "h").hit
        # ...and with a zero override the same payload is stale. This is --force.
        forced = cache.get("fred", "series/observations", "h", ttl_override=0)
        assert not forced.hit
        assert forced.reason == "stale"

    def test_zero_override_is_counted_as_a_miss(self, store):
        """Test that zero override is counted as a miss."""
        _stored(store, request_hash="h", ttl_seconds=3600)
        cache = TTLCache(store)
        cache.get("fred", "series/observations", "h", ttl_override=0)
        assert cache.stats()["misses"] == 1.0
        assert cache.stats()["hits"] == 0.0

    def test_override_can_rescue_an_expired_payload(self, store):
        """A larger override extends the window beyond the stored TTL."""
        _stored(store, request_hash="h", age_hours=3, ttl_seconds=60)
        cache = TTLCache(store)
        assert not cache.get("fred", "series/observations", "h").hit
        relaxed = cache.get("fred", "series/observations", "h", ttl_override=86400)
        assert relaxed.hit

    def test_ttl_for_resolution(self, store):
        """Test that ttl for resolution."""
        cache = TTLCache(store, default_ttl_s=100)
        assert cache.ttl_for() == 100
        assert cache.ttl_for(0) == 0
        assert cache.ttl_for(5) == 5

class TestStatistics:
    """Test statistics."""
    def test_hit_ratio_tracks_hits_and_misses(self, store):
        """Test that hit ratio tracks hits and misses."""
        _stored(store, request_hash="h")
        cache = TTLCache(store)
        cache.get("fred", "series/observations", "h")          # hit
        cache.get("fred", "series/observations", "missing")     # miss
        stats = cache.stats()
        assert stats["hits"] == 1.0
        assert stats["misses"] == 1.0
        assert stats["hit_ratio"] == 0.5

    def test_steady_state_hit_ratio_exceeds_sc003(self, store):
        """At steady state a scheduled run should clear the 80% bar."""
        for i in range(9):
            _stored(store, request_hash=f"h{i}")
        cache = TTLCache(store)
        for i in range(9):
            cache.get("fred", "series/observations", f"h{i}")   # 9 hits
        for i in range(2):
            cache.get("fred", "series/observations", f"miss{i}")  # 2 misses
        stats = cache.stats()
        assert stats["hit_ratio"] >= 0.8, "SC-003 requires >= 80% at steady state"

    def test_metrics_are_recorded(self, store):
        """Test that metrics are recorded."""
        from src.observability.metrics import get_metrics, reset

        reset()
        _stored(store, request_hash="h")
        cache = TTLCache(store)
        cache.get("fred", "series/observations", "h")
        cache.get("fred", "series/observations", "missing")
        m = get_metrics()
        assert m.cache_hits is not None and m.cache_misses is not None

    def test_reset_stats(self, store):
        """Test that reset stats."""
        _stored(store, request_hash="h")
        cache = TTLCache(store)
        cache.get("fred", "series/observations", "h")
        cache.reset_stats()
        assert cache.stats()["total"] == 0.0
