"""News adapter — unstructured text for the sentiment engine (T036, FR-006).

Supplies the input the Sentiment Engine scores. Three providers carry news on
their free tiers (spec §3.2.1): Finnhub (1 year of history), FMP, and Tiingo
(3 months queryable).

**Alpha Vantage's `NEWS_SENTIMENT` is deliberately excluded** (spec §3.2): it
returns vendor-computed sentiment, which would make the open-source Sentiment
Engine redundant and violate Principle I's spirit — the sentiment must be
Aetheris' own, not a paid API's.
"""

from __future__ import annotations

from collections.abc import Mapping

from src.ingestion.base import AdapterError, FetchRequest
from src.ingestion.market_client import BASE_URLS

ENDPOINTS: dict[str, str] = {
    "finnhub": "/news",
    "fmp": "fmp/articles",
    "tiingo": "tiingo/news",
}


class NewsAdapter:
    """News articles and headlines for one provider (set per fetch)."""

    def __init__(self, provider: str, api_key: str | None = None) -> None:
        if provider not in ENDPOINTS:
            raise AdapterError(
                f"news adapter does not serve {provider!r}; "
                f"expected one of {sorted(ENDPOINTS)}"
            )
        self.provider = provider
        self._api_key = api_key

    # -- ProviderAdapter protocol ---------------------------------------
    def can_handle(self, endpoint: str) -> bool:
        return endpoint == ENDPOINTS[self.provider]

    @property
    def endpoint(self) -> str:
        return ENDPOINTS[self.provider]

    def build_request(
        self, endpoint: str, params: Mapping[str, str]
    ) -> FetchRequest:
        if not self.can_handle(endpoint):
            raise AdapterError(
                f"{self.provider} news adapter cannot serve {endpoint!r}; "
                f"expected {self.endpoint!r}"
            )

        merged = dict(params)
        base = BASE_URLS[self.provider]
        url = f"{base}/{endpoint}"

        if self.provider == "finnhub":
            # Finnhub news is a company feed when `symbol` is present, otherwise
            # the general market feed. The symbol must be part of the identity.
            merged.setdefault("category", "general")
            merged.pop("token", None)
            if self._api_key:
                merged["token"] = self._api_key
        elif self.provider == "fmp":
            merged.pop("apikey", None)
            if self._api_key:
                merged["apikey"] = self._api_key
        elif self.provider == "tiingo":
            merged.pop("token", None)
            if self._api_key:
                merged["token"] = self._api_key

        return FetchRequest.create(self.provider, endpoint, url, merged)

    def headers(self) -> Mapping[str, str]:
        headers = {"Accept": "application/json"}
        if self.provider == "tiingo" and self._api_key:
            headers["Authorization"] = f"Token {self._api_key}"
        return headers


__all__ = ["NewsAdapter", "ENDPOINTS"]
