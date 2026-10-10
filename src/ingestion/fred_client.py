"""FRED adapter — U.S. macroeconomic series (T034, FR-004).

Covers the three series named in spec §3.2: GDP, CPI, and the Federal Funds
Rate, all served from the single `series/observations` endpoint.

FRED is the only provider with no fallback (spec §3.2.1, fallback_order = None):
if FRED is unavailable, macro data cannot be substituted, so the registry
surfaces the failure instead of silently serving prices in its place.
"""

from __future__ import annotations

from collections.abc import Mapping

from src.ingestion.base import AdapterError, FetchRequest

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# The three series mandated by spec §3.2.
SUPPORTED_SERIES: tuple[str, ...] = ("GDP", "CPIAUCSL", "FEDFUNDS")

ENDPOINT = "series/observations"


class FredAdapter:
    """Fred adapter."""
    provider = "fred"

    def __init__(self, api_key: str | None = None) -> None:
        """Initialise the instance."""
        self._api_key = api_key

    # -- ProviderAdapter protocol ---------------------------------------
    def can_handle(self, endpoint: str) -> bool:
        """Return whether the handle is possible."""
        return endpoint == ENDPOINT

    def build_request(
        self, endpoint: str, params: Mapping[str, str]
    ) -> FetchRequest:
        """Build the request."""
        if endpoint != ENDPOINT:
            raise AdapterError(
                f"fred adapter cannot serve {endpoint!r}; expected {ENDPOINT!r}"
            )
        merged = {
            "file_type": "json",
            "sort_order": "asc",
            **dict(params),
        }
        series_id = merged.get("series_id")
        if not series_id:
            raise AdapterError(
                "fred requires a `series_id` parameter "
                f"(supported: {', '.join(SUPPORTED_SERIES)})"
            )
        if self._api_key:
            merged["api_key"] = self._api_key
        return FetchRequest.create(self.provider, endpoint, BASE_URL, merged)

    def headers(self) -> Mapping[str, str]:
        """Handle the headers."""
        return {"Accept": "application/json"}

    # -- helpers --------------------------------------------------------
    def series_request(self, series_id: str, **params: str) -> FetchRequest:
        """Build a request for one of the three mandated macro series."""
        return self.build_request(ENDPOINT, {"series_id": series_id, **params})


__all__ = ["FredAdapter", "SUPPORTED_SERIES", "ENDPOINT", "BASE_URL"]
