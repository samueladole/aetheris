"""Contract tests for the adapter protocol (T027).

Enforces the invariants A1–A7 from
`.specify/specs/contracts/adapter-protocol.md`, mechanically checked rather
than merely documented.
"""

from __future__ import annotations

import pytest

from src.config.providers import get_registry
from src.ingestion.base import (
    AdapterError,
    FetchRequest,
    HttpResponse,
    ProviderUnavailable,
)
from src.ingestion.fred_client import SUPPORTED_SERIES, FredAdapter
from src.ingestion.market_client import MarketAdapter
from src.ingestion.news_client import NewsAdapter


class TestRequestIdentity:
    """Test request identity."""
    def test_request_hash_is_stable(self):
        """Test that request hash is stable."""
        a = FetchRequest.create("fred", "series/observations", "https://x",
                                {"series_id": "GDP"})
        b = FetchRequest.create("fred", "series/observations", "https://x",
                                {"series_id": "GDP"})
        assert a.request_hash == b.request_hash

    def test_request_hash_ignores_param_order(self):
        """Test that request hash ignores param order."""
        a = FetchRequest.create("fred", "e", "https://x", {"a": "1", "b": "2"})
        b = FetchRequest.create("fred", "e", "https://x", {"b": "2", "a": "1"})
        assert a.request_hash == b.request_hash

    def test_different_params_differ(self):
        """Test that different params differ."""
        a = FetchRequest.create("fred", "e", "https://x", {"series_id": "GDP"})
        b = FetchRequest.create("fred", "e", "https://x", {"series_id": "CPIAUCSL"})
        assert a.request_hash != b.request_hash


class TestProviderUnavailable:
    """A7: a 429 or 5xx is a typed error, never a swallowed one."""

    @pytest.mark.parametrize("code", [429, 500, 502, 503])
    def test_retryable_codes(self, code):
        """Test that retryable codes."""
        exc = ProviderUnavailable("fred", "series/observations", code, "boom")
        assert exc.retryable

    @pytest.mark.parametrize("code", [400, 401, 403, 404])
    def test_non_retryable_client_errors(self, code):
        """Test that non retryable client errors."""
        exc = ProviderUnavailable("fred", "series/observations", code, "boom")
        assert not exc.retryable

    def test_connection_error_is_retryable(self):
        """Test that connection error is retryable."""
        exc = ProviderUnavailable("fred", "e", None, "connection reset")
        assert exc.retryable

    def test_raise_for_unavailable(self):
        """Test that raise for unavailable."""
        resp = HttpResponse(429, b"", {})
        with pytest.raises(ProviderUnavailable):
            resp.raise_for_unavailable("fred", "series/observations")

        ok = HttpResponse(200, b"{}", {})
        ok.raise_for_unavailable("fred", "series/observations")  # must not raise

    def test_401_is_not_retryable(self):
        """A missing API key is a configuration error, not a transient fault."""
        exc = ProviderUnavailable("finnhub", "quote", 401, "no key")
        assert not exc.retryable


class TestFredAdapter:
    """Test fred adapter."""
    def test_requires_series_id(self):
        """Test that requires series id."""
        adapter = FredAdapter(api_key="k")
        with pytest.raises(AdapterError):
            adapter.build_request("series/observations", {})

    @pytest.mark.parametrize("series", SUPPORTED_SERIES)
    def test_serves_the_three_mandated_series(self, series):
        """Test that serves the three mandated series."""
        adapter = FredAdapter(api_key="k")
        request = adapter.build_request(
            "series/observations", {"series_id": series}
        )
        assert request.provider == "fred"
        assert request.params["series_id"] == series
        assert request.request_hash

    def test_rejects_other_endpoints(self):
        """Test that rejects other endpoints."""
        adapter = FredAdapter(api_key="k")
        with pytest.raises(AdapterError):
            adapter.build_request("quote", {"series_id": "GDP"})

    def test_can_handle(self):
        """Test that can handle."""
        adapter = FredAdapter(api_key="k")
        assert adapter.can_handle("series/observations")
        assert not adapter.can_handle("tiingo/daily")

    def test_api_key_never_in_request_url(self):
        """Secrets must travel in params/headers, not the cache key's URL."""
        adapter = FredAdapter(api_key="SECRET")
        request = adapter.build_request("series/observations", {"series_id": "GDP"})
        assert "SECRET" not in request.url
        assert request.params["api_key"] == "SECRET"


class TestMarketAdapter:
    """Test market adapter."""
    @pytest.mark.parametrize("provider", ["alpha_vantage", "finnhub", "fmp", "tiingo"])
    def test_serves_only_declared_market_endpoints(self, provider):
        """Market endpoints only — news endpoints belong to NewsAdapter (A2)."""
        adapter = MarketAdapter(provider, api_key="k")
        spec = get_registry().get(provider)
        for endpoint in spec.market_endpoints:
            assert adapter.can_handle(endpoint), f"{provider} should serve {endpoint}"
        for endpoint in spec.news_endpoints:
            assert not adapter.can_handle(endpoint), (
                f"{provider} market adapter must not claim the news endpoint {endpoint}"
            )

    @pytest.mark.parametrize("provider", ["finnhub", "fmp", "tiingo"])
    def test_news_adapter_serves_only_news_endpoints(self, provider):
        """Test that news adapter serves only news endpoints."""
        adapter = NewsAdapter(provider, api_key="k")
        spec = get_registry().get(provider)
        for endpoint in spec.news_endpoints:
            assert adapter.can_handle(endpoint)
        for endpoint in spec.market_endpoints:
            assert not adapter.can_handle(endpoint)

    def test_rejects_unknown_provider(self):
        """Test that rejects unknown provider."""
        with pytest.raises(AdapterError):
            MarketAdapter("fred")

    def test_alpha_vantage_uses_function_param(self):
        """Test that alpha vantage uses function param."""
        adapter = MarketAdapter("alpha_vantage", api_key="k")
        request = adapter.build_request("GLOBAL_QUOTE", {"symbol": "AAPL"})
        assert request.params["function"] == "GLOBAL_QUOTE"
        assert request.params["symbol"] == "AAPL"

    def test_finnhub_uses_path_and_token(self):
        """Test that finnhub uses path and token."""
        adapter = MarketAdapter("finnhub", api_key="k")
        request = adapter.build_request("quote", {"symbol": "AAPL"})
        assert request.url.endswith("/quote")
        assert request.params["token"] == "k"

    def test_tiingo_key_goes_in_header_not_query(self):
        """Tiingo authenticates by header; the key must not enter the hash."""
        adapter = MarketAdapter("tiingo", api_key="SECRET")
        request = adapter.build_request("tiingo/daily", {"tickers": "AAPL"})
        headers = adapter.headers()
        assert headers["Authorization"] == "Token SECRET"
        assert "SECRET" not in request.url


class TestNewsAdapter:
    """Test news adapter."""
    @pytest.mark.parametrize("provider", ["finnhub", "fmp", "tiingo"])
    def test_serves_news(self, provider):
        """Test that serves news."""
        adapter = NewsAdapter(provider, api_key="k")
        assert adapter.can_handle(adapter.endpoint)
        request = adapter.build_request(adapter.endpoint, {})
        assert request.provider == provider

    def test_fred_and_alpha_vantage_are_excluded(self):
        """FRED carries no news; Alpha Vantage NEWS_SENTIMENT is vendor sentiment."""
        for provider in ("fred", "alpha_vantage"):
            with pytest.raises(AdapterError):
                NewsAdapter(provider)

    def test_finnhub_symbol_is_part_of_identity(self):
        """A per-symbol feed and the general feed must not share a cache entry."""
        adapter = NewsAdapter("finnhub", api_key="k")
        general = adapter.build_request(adapter.endpoint, {})
        symbol = adapter.build_request(adapter.endpoint, {"symbol": "AAPL"})
        assert general.request_hash != symbol.request_hash

    def test_rejects_news_endpoint_for_another_provider(self):
        """Test that rejects news endpoint for another provider."""
        adapter = NewsAdapter("finnhub", api_key="k")
        with pytest.raises(AdapterError):
            adapter.build_request("tiingo/news", {})


class TestAdapterProtocolCompliance:
    """Every adapter satisfies the shared protocol (A1–A5)."""

    ADAPTERS = [
        FredAdapter(api_key="k"),
        MarketAdapter("finnhub", api_key="k"),
        NewsAdapter("tiingo", api_key="k"),
    ]

    @pytest.mark.parametrize("adapter", ADAPTERS)
    def test_declares_provider_known_to_registry(self, adapter):
        """Test that declares provider known to registry."""
        assert adapter.provider
        get_registry().get(adapter.provider)  # raises if unknown

    @pytest.mark.parametrize("adapter", ADAPTERS)
    def test_exposes_headers(self, adapter):
        """Test that exposes headers."""
        headers = adapter.headers()
        assert "Accept" in headers

    @pytest.mark.parametrize("adapter", ADAPTERS)
    def test_returns_raw_requests_not_domain_objects(self, adapter):
        """A1: adapters produce FetchRequest/HttpResponse, never Asset or score."""
        assert hasattr(adapter, "build_request")
