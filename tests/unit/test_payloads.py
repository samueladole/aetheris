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


def test_request_params_round_trip(tmp_path):
    """Request parameters survive a store and a get."""
    store = RawPayloadStore(str(tmp_path / "raw.db"))
    p, _ = store.store(
        provider="fred",
        endpoint="series/observations",
        request_hash="h",
        body=b"{}",
        request_params={"series_id": "GDP"},
    )
    assert store.get(p.payload_id).request_params == {"series_id": "GDP"}
    store.close()


def test_existing_database_gains_the_column(tmp_path):
    """A pre-change database is upgraded in place and old rows read back with None."""
    import sqlite3

    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE raw_payload (
            payload_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
            endpoint TEXT NOT NULL, request_hash TEXT NOT NULL,
            schema_version INTEGER NOT NULL, body BLOB NOT NULL,
            fetched_at TEXT NOT NULL, ttl_seconds INTEGER
        );
        INSERT INTO raw_payload VALUES
            ('old-id', 'fred', 'ep', 'h', 1, x'7b7d', '2026-01-01T00:00:00+00:00', NULL);
        """
    )
    conn.commit()
    conn.close()

    store = RawPayloadStore(path)
    old = store.get("old-id")
    assert old is not None
    assert old.request_params is None
    store.close()


def test_latest_by_endpoint_returns_newest_per_request(tmp_path):
    """Only the newest payload per request hash is returned, newest first."""
    from datetime import datetime

    store = RawPayloadStore(str(tmp_path / "raw.db"))
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    for h, offset in (("a", 0), ("a", 2), ("b", 1)):
        store.store(
            provider="fred",
            endpoint="e",
            request_hash=h,
            body=b"{}",
            fetched_at=t0 + timedelta(hours=offset),
        )
    result = store.latest_by_endpoint("fred", "e")
    assert [(r.request_hash, r.fetched_at) for r in result] == [
        ("a", t0 + timedelta(hours=2)),
        ("b", t0 + timedelta(hours=1)),
    ]
    store.close()


def test_registry_never_persists_credentials(tmp_path):
    """The stored params are the caller's, never the adapter-merged credentials."""
    from src.ingestion.base import HttpResponse
    from src.ingestion.market_client import MarketAdapter
    from src.ingestion.ratelimit import SharedRateLimiter
    from src.ingestion.registry import IngestionRegistry

    class Http:
        def get(self, url, *, params, headers, timeout):
            """Return a canned quote."""
            return HttpResponse(200, b'{"c":1}', {})

    store = RawPayloadStore(str(tmp_path / "raw.db"))
    reg = IngestionRegistry(
        payload_store=store,
        cache=TTLCache(store, default_ttl_s=3600),
        http=Http(),
        rate_limiter=SharedRateLimiter(),
        activity_timeout_s=1.0,
    )
    reg.register_adapter(MarketAdapter("finnhub", api_key="secret-key-123456"))
    outcome = reg.fetch("prices", endpoint="quote", params={"symbol": "AAPL"})
    stored = store.get(outcome.payload.payload_id)
    assert stored.request_params == {"symbol": "AAPL"}
    assert all("secret-key-123456" not in v for v in stored.request_params.values())
    store.close()
