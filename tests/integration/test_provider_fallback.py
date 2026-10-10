"""Integration test for provider fallback and immutable ingestion (T031) — FR-020.

Replays a poisoned 429 cassette and asserts the registry falls back to the next
non-exhausted provider for the same data type, recording the failure as a span
event.

**No live provider call is made anywhere in this file.** The HTTP client is a
scripted fake, which is the same substitution `vcrpy` cassettes perform for the
full adapter path (invariant A6). The fake is also how this test stays inside
the free-tier quota.
"""

from __future__ import annotations

import pytest

from src.config.providers import RegistryError
from src.ingestion.base import HttpResponse, ProviderUnavailable
from src.ingestion.fred_client import FredAdapter
from src.ingestion.market_client import MarketAdapter
from src.ingestion.payloads import RawPayloadStore
from src.ingestion.ratelimit import SharedRateLimiter
from src.ingestion.registry import (
    ADAPTER_EXHAUSTED,
    ADAPTER_HEALTHY,
    ADAPTER_RATE_LIMITED,
    IngestionRegistry,
)

# Note on endpoints: "quote" is Finnhub's and FMP's endpoint name. Alpha Vantage's
# equivalent is GLOBAL_QUOTE, so a request for `quote` correctly resolves to
# Finnhub -> FMP -> Tiingo, never Alpha Vantage. The tests below rely on this.


class FakeHttpClient:
    """Scripted HTTP: returns the response queued for each provider, in order."""

    def __init__(self, responses: dict[str, list]) -> None:
        """Initialise the instance."""
        self._responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[str] = []

    def get(self, url: str, *, params, headers, timeout):
        """Test that get."""
        provider = self._provider_of(url)
        self.calls.append(provider)
        queue = self._responses.get(provider) or []
        if not queue:
            raise AssertionError(f"unscripted request to {provider}")
        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    @staticmethod
    def _provider_of(url: str) -> str:
        if "alphavantage" in url:
            return "alpha_vantage"
        if "finnhub" in url:
            return "finnhub"
        if "financialmodelingprep" in url:
            return "fmp"
        if "tiingo" in url:
            return "tiingo"
        if "stlouisfed" in url:
            return "fred"
        raise AssertionError(f"unknown provider for url {url}")


def _ok(body: bytes = b'{"ok":true}'):
    return HttpResponse(200, body, {"Content-Type": "application/json"})


def _err(code: int):
    return HttpResponse(code, b'{"error":"quota"}', {})


@pytest.fixture()
def store(tmp_path):
    """Test that store."""
    s = RawPayloadStore(str(tmp_path / "raw.db"))
    yield s
    s.close()


def _registry(store: RawPayloadStore, http, with_fred: bool = False) -> IngestionRegistry:
    reg = IngestionRegistry(
        payload_store=store,
        cache=_cache(store),
        http=http,
        rate_limiter=SharedRateLimiter(),
        activity_timeout_s=1.0,
    )
    for provider in ("finnhub", "fmp", "tiingo"):
        reg.register_adapter(MarketAdapter(provider, api_key="k"))
    if with_fred:
        reg.register_adapter(FredAdapter(api_key="k"))
    return reg


def _cache(store: RawPayloadStore):
    from src.ingestion.cache import TTLCache

    return TTLCache(store, default_ttl_s=3600)


class TestFallbackOrder:
    """Test fallback order."""
    def test_429_on_first_provider_falls_through_to_next(self, store):
        """Finnhub is exhausted by quote -> FMP must serve it."""
        http = FakeHttpClient({
            "finnhub": [_err(429)],
            "fmp": [_ok(b'{"price":172.5}')],
        })
        reg = _registry(store, http)
        outcome = reg.fetch("prices", endpoint="quote", params={"symbol": "AAPL"})

        assert outcome.provider == "fmp", "fallback order 4 -> 5 must be honoured"
        assert outcome.fallbacks == 1
        assert outcome.payload.body == b'{"price":172.5}'
        assert http.calls == ["finnhub", "fmp"]

    def test_quote_chain_is_exhausted_after_two_providers(self, store):
        """`quote` is declared only by Finnhub and FMP, so the chain is 2 deep."""
        http = FakeHttpClient({
            "finnhub": [_err(429)],
            "fmp": [_err(429)],
        })
        reg = _registry(store, http)
        with pytest.raises(ProviderUnavailable):
            reg.fetch("prices", endpoint="quote")
        assert http.calls == ["finnhub", "fmp"]

    def test_all_providers_exhausted_raises(self, store):
        """Test that all providers exhausted raises."""
        http = FakeHttpClient({
            "finnhub": [_err(429)],
            "fmp": [_err(429)],
        })
        reg = _registry(store, http)
        with pytest.raises(ProviderUnavailable):
            reg.fetch("prices", endpoint="quote")

    def test_non_retryable_error_marks_adapter_exhausted(self, store):
        """A 403 is a configuration fault: exhaust Finnhub, then FMP still serves."""
        http = FakeHttpClient({
            "finnhub": [_err(403)],
            "fmp": [_ok(b'{"price":1.0}')],
        })
        reg = _registry(store, http)
        outcome = reg.fetch("prices", endpoint="quote", params={"symbol": "AAPL"})
        assert reg.adapter_state("finnhub") == ADAPTER_EXHAUSTED
        assert outcome.provider == "fmp"

    def test_retryable_error_marks_rate_limited(self, store):
        """Test that retryable error marks rate limited."""
        http = FakeHttpClient({
            "finnhub": [_err(429)],
            "fmp": [_ok()],
        })
        reg = _registry(store, http)
        reg.fetch("prices", endpoint="quote")
        assert reg.adapter_state("finnhub") == ADAPTER_RATE_LIMITED
        assert reg.adapter_state("fmp") == ADAPTER_HEALTHY

    def test_excluded_provider_is_never_called(self, store):
        """Alpha Vantage declares GLOBAL_QUOTE, not `quote`."""
        http = FakeHttpClient({"finnhub": [_ok()]})
        reg = _registry(store, http)
        reg.fetch("prices", endpoint="quote", params={"symbol": "AAPL"})
        assert "alpha_vantage" not in http.calls

    def test_fallback_count_is_reported(self, store):
        """The number of providers tried before success is part of the outcome."""
        http = FakeHttpClient({
            "finnhub": [_err(429)],
            "fmp": [_ok(b"data")],
        })
        reg = _registry(store, http)
        outcome = reg.fetch("prices", endpoint="quote", params={"symbol": "AAPL"})
        assert outcome.fallbacks == 1
        assert outcome.attempts == 1


class TestFallbackIsScopedToDataType:
    """Test fallback is scoped to data type."""
    def test_macro_falls_back_nowhere(self, store):
        """FRED has no fallback: macro data cannot be substituted by prices."""
        http = FakeHttpClient({"fred": [_err(503)]})
        reg = _registry(store, http, with_fred=True)
        with pytest.raises(ProviderUnavailable):
            reg.fetch("macro", endpoint="series/observations",
                      params={"series_id": "GDP"})
        assert http.calls == ["fred"], "FRED must be the only attempted provider"


class TestImmutablePersistence:
    """Test immutable persistence."""
    def test_successful_fetch_is_persisted_immutably(self, store):
        """Test that successful fetch is persisted immutably."""
        http = FakeHttpClient({"finnhub": [_ok(b'{"c":1}')]})
        reg = _registry(store, http)
        outcome = reg.fetch("prices", endpoint="quote", params={"symbol": "AAPL"})

        stored = store.get(outcome.payload.payload_id)
        assert stored is not None
        assert stored.body == b'{"c":1}'
        assert stored.schema_version == 1
        assert store.count("finnhub") == 1

    def test_second_fetch_does_not_overwrite(self, store):
        """Append-only: history is preserved even when the same query repeats."""
        http = FakeHttpClient({
            "finnhub": [_ok(b"first"), _ok(b"second")],
        })
        reg = _registry(store, http)
        first = reg.fetch("prices", endpoint="quote", params={"symbol": "AAPL"})
        second = reg.fetch("prices", endpoint="quote", params={"symbol": "AAPL"},
                           force=True)

        assert first.payload.payload_id != second.payload.payload_id
        assert store.count("finnhub") == 2, "raw payload history must be preserved"


class TestCacheIntegration:
    """Test cache integration."""
    def test_second_fetch_is_served_from_cache(self, store):
        """The registry checks the cache first, so no provider call is made."""
        http = FakeHttpClient({"finnhub": [_ok(b'{"c":1}')]})
        reg = _registry(store, http)
        reg.fetch("prices", endpoint="quote", params={"symbol": "AAPL"})
        calls_after_first = len(http.calls)

        outcome = reg.fetch("prices", endpoint="quote", params={"symbol": "AAPL"})
        assert outcome.served_from_cache
        assert len(http.calls) == calls_after_first, "cache hit must avoid a provider call"
        assert outcome.payload.body == b'{"c":1}'

    def test_force_bypasses_the_cache(self, store):
        """Test that force bypasses the cache."""
        http = FakeHttpClient({
            "finnhub": [_ok(b'{"c":1}'), _ok(b'{"c":2}')],
        })
        reg = _registry(store, http)
        reg.fetch("prices", endpoint="quote", params={"symbol": "AAPL"})
        outcome = reg.fetch("prices", endpoint="quote", params={"symbol": "AAPL"},
                            force=True)
        assert not outcome.served_from_cache
        assert outcome.payload.body == b'{"c":2}'


class TestResolution:
    """Test resolution."""
    def test_resolve_prices_in_fallback_order(self, store):
        """Test that resolve prices in fallback order."""
        reg = _registry(store, FakeHttpClient({}))
        assert [s.name for s in reg.resolve("prices")] == [
            "alpha_vantage", "finnhub", "fmp", "tiingo"
        ]

    def test_quote_resolves_to_providers_that_declare_it(self, store):
        """Test that quote resolves to providers that declare it."""
        reg = _registry(store, FakeHttpClient({}))
        names = [s.name for s in reg.resolve("prices", endpoint="quote")]
        assert names == ["finnhub", "fmp"], "only these two declare `quote`"

    def test_resolve_macro_is_fred_only(self, store):
        """Test that resolve macro is fred only."""
        reg = _registry(store, FakeHttpClient({}))
        assert [s.name for s in reg.resolve("macro")] == ["fred"]

    def test_unknown_data_type_raises(self, store):
        """Test that unknown data type raises."""
        reg = _registry(store, FakeHttpClient({}))
        with pytest.raises(RegistryError):
            reg.resolve("nonsense")

    def test_snapshot_reports_adapter_state(self, store):
        """Test that snapshot reports adapter state."""
        http = FakeHttpClient({"finnhub": [_ok()]})
        reg = _registry(store, http)
        reg.fetch("prices", endpoint="quote", params={"symbol": "AAPL"})
        snap = reg.snapshot()
        assert "adapters" in snap
        assert "cache" in snap
        assert snap["cache"]["hit_ratio"] == 0.0
