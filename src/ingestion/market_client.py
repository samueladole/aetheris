"""Market adapter — prices and fundamentals (T035, FR-005).

Serves stock, forex, and crypto prices plus fundamentals from four of the five
providers named in spec §3.2.1 (Alpha Vantage, Finnhub, FMP, Tiingo). FRED is
excluded here because it carries no market data.

This is a single adapter instance that dispatches per provider: the registry
selects the provider spec, and the adapter shapes the request for whichever
provider it is currently acting as. That is why ``provider`` is settable rather
than a class constant — the adapters are the *thin* part, and provider choice
belongs to the registry (invariant A2).
"""

from __future__ import annotations

from collections.abc import Mapping

from src.ingestion.base import AdapterError, FetchRequest

# Base URLs per provider, from the spec §3.2.1 registry.
#
# Tiingo's base is the API root, not `/tiingo`: its paths are already
# `/tiingo/daily/...`, `/tiingo/news`, and `/iex/...`. Including `/tiingo` in the base
# produced `https://api.tiingo.com/tiingo/tiingo/daily`, a 404.
BASE_URLS: dict[str, str] = {
    "alpha_vantage": "https://www.alphavantage.co/query",
    "finnhub": "https://finnhub.io/api/v1",
    "fmp": "https://financialmodelingprep.com/api/v3",
    "tiingo": "https://api.tiingo.com",
}

# Endpoints each provider exposes, mirroring spec §3.2.1.
PROVIDER_ENDPOINTS: dict[str, tuple[str, ...]] = {
    "alpha_vantage": ("GLOBAL_QUOTE", "FX_DAILY", "CRYPTO_RATING"),
    "finnhub": ("quote", "stock/profile2"),
    "fmp": ("quote", "historical-price-full"),
    "tiingo": ("tiingo/daily", "iex"),
}


class MarketAdapter:
    """Prices and fundamentals for one provider (set per fetch)."""

    def __init__(self, provider: str, api_key: str | None = None) -> None:
        """Initialise the instance."""
        if provider not in BASE_URLS:
            raise AdapterError(
                f"market adapter does not serve {provider!r}; "
                f"expected one of {sorted(BASE_URLS)}"
            )
        self.provider = provider
        self._api_key = api_key

    # -- ProviderAdapter protocol ---------------------------------------
    def can_handle(self, endpoint: str) -> bool:
        """Return whether the handle is possible."""
        return endpoint in PROVIDER_ENDPOINTS[self.provider]

    def build_request(
        self, endpoint: str, params: Mapping[str, str]
    ) -> FetchRequest:
        """Build the request."""
        if not self.can_handle(endpoint):
            raise AdapterError(
                f"{self.provider} cannot serve {endpoint!r}; "
                f"supported: {PROVIDER_ENDPOINTS[self.provider]}"
            )

        merged = dict(params)
        base = BASE_URLS[self.provider]

        # Alpha Vantage routes everything through ?function=<ENDPOINT>.
        if self.provider == "alpha_vantage":
            merged.setdefault("function", endpoint)
            url = base
            merged.pop("apikey", None)
            if self._api_key:
                merged["apikey"] = self._api_key
        # Finnhub uses a path segment and a token query parameter.
        elif self.provider == "finnhub":
            url = f"{base}/{endpoint}"
            merged.pop("token", None)
            if self._api_key:
                merged["token"] = self._api_key
        # FMP and Tiingo both use path + optional key.
        else:
            # Tiingo identifies the instrument in the PATH, not query params:
            # GET /tiingo/daily/{ticker}. Leaving it in the query returns 404.
            if self.provider == "tiingo":
                ticker = merged.pop("tickers", None)
                url = f"{base}/{endpoint}/{ticker}" if ticker else f"{base}/{endpoint}"
            else:
                url = f"{base}/{endpoint}"
            if self._api_key and self.provider == "fmp":
                merged.setdefault("apikey", self._api_key)

        if self.provider == "tiingo" and self._api_key:
            # Tiingo authenticates via header; keep it out of the request hash
            # so the same query from the same key does not double-store.
            merged.pop("token", None)

        return FetchRequest.create(self.provider, endpoint, url, merged)

    def headers(self) -> Mapping[str, str]:
        """Handle the headers."""
        headers = {"Accept": "application/json"}
        if self.provider == "tiingo" and self._api_key:
            headers["Authorization"] = f"Token {self._api_key}"
        return headers


__all__ = ["MarketAdapter", "BASE_URLS", "PROVIDER_ENDPOINTS"]
