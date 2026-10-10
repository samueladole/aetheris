"""Unit tests for the immutable raw payload store and TTL cache (T030).

Verifies FR-015 (immutable, schema-versioned raw payloads) and VR-8
(append-only, never overwritten).
"""

from __future__ import annotations

from datetime import UTC, timedelta

import pytest

from src.ingestion.cache import TTLCache
from src.ingestion.payloads import RawPayloadStore, build_payload_id


@pytest.fixture()
def store(tmp_path):
    """Test that store."""
    s = RawPayloadStore(str(tmp_path / "raw.db"))
    yield s
    s.close()


class TestRawPayloadStore:
    """Test raw payload store."""
    def test_store_and_retrieve(self, store):
        """Test that store and retrieve."""
        payload, created = store.store(
            provider="fred",
            endpoint="series/observations",
            request_hash="abc123",
            body=b'{"a":1}',
        )
        assert created
        assert payload.schema_version == 1
        assert payload.body == b'{"a":1}'
        assert store.get(payload.payload_id) is not None
        assert store.count("fred") == 1

    def test_append_only_history_is_preserved(self, store):
        """Each fetch appends a distinct row; nothing is overwritten."""
        p1, _ = store.store(
            provider="fred",
            endpoint="series/observations",
            request_hash="h",
            body=b"first",
        )
        p2, _ = store.store(
            provider="fred",
            endpoint="series/observations",
            request_hash="h",
            body=b"second",
        )
        assert p1.payload_id != p2.payload_id
        assert p1.body == b"first"
        assert p2.body == b"second"
        assert store.count("fred") == 2, "history must not be overwritten"

    def test_latest_returns_most_recent(self, store):
        """Test that latest returns most recent."""
        store.store(
            provider="fred",
            endpoint="series/observations",
            request_hash="h",
            body=b"old",
        )
        store.store(
            provider="fred",
            endpoint="series/observations",
            request_hash="h",
            body=b"new",
        )
        latest = store.latest("fred", "series/observations", "h")
        assert latest is not None
        assert latest.body == b"new"

    def test_payload_id_format(self):
        """Test that payload id format."""
        from datetime import datetime

        ts = datetime(2026, 10, 10, 14, 30, tzinfo=UTC)
        pid = build_payload_id("fred", "series/observations", "h", ts)
        assert pid == "fred:series/observations:h:2026-10-10T14:30:00+00:00"

    def test_count_filters_by_provider(self, store):
        """Test that count filters by provider."""
        store.store(provider="fred", endpoint="e", request_hash="h", body=b"")
        store.store(provider="tiingo", endpoint="e", request_hash="h", body=b"")
        assert store.count("fred") == 1
        assert store.count("tiingo") == 1


class TestTTLCache:
    """Test ttlcache."""
    def test_fresh_payload_is_a_hit(self, store):
        """Test that fresh payload is a hit."""
        store.store(
            provider="fred",
            endpoint="series/observations",
            request_hash="h",
            body=b"x",
            ttl_seconds=3600,
        )
        cache = TTLCache(store)
        outcome = cache.get("fred", "series/observations", "h")
        assert outcome.hit
        assert outcome.reason == "fresh"
        assert outcome.payload is not None

    def test_absent_is_a_miss(self, store):
        """Test that absent is a miss."""
        cache = TTLCache(store)
        outcome = cache.get("fred", "series/observations", "nope")
        assert not outcome.hit
        assert outcome.reason == "absent"

    def test_expired_is_a_miss(self, store):
        """Test that expired is a miss."""
        from datetime import datetime

        past = datetime.now(UTC) - timedelta(hours=2)
        store.store(
            provider="fred",
            endpoint="series/observations",
            request_hash="h",
            body=b"x",
            fetched_at=past,
            ttl_seconds=60,
        )
        cache = TTLCache(store)
        outcome = cache.get("fred", "series/observations", "h")
        assert not outcome.hit
        assert outcome.reason == "stale"

    def test_hit_ratio_tracks_hits_and_misses(self, store):
        """Test that hit ratio tracks hits and misses."""
        cache = TTLCache(store)
        store.store(provider="fred", endpoint="e", request_hash="h", body=b"x")
        cache.get("fred", "e", "h")  # hit
        cache.get("fred", "e", "missing")  # miss
        stats = cache.stats()
        assert stats["hits"] == 1.0
        assert stats["misses"] == 1.0
        assert stats["hit_ratio"] == 0.5

    def test_ttl_for_resolves_override(self, store):
        """Test that ttl for resolves override."""
        cache = TTLCache(store, default_ttl_s=100)
        assert cache.ttl_for() == 100
        assert cache.ttl_for(5) == 5
