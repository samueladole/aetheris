"""FRED adapter — U.S. macroeconomic series (T034, FR-004).

Covers the three series named in spec §3.2: GDP, CPI, and the Federal Funds
Rate, all served from the single `series/observations` endpoint.

FRED is the only provider with no fallback (spec §3.2.1, fallback_order = None):
if FRED is unavailable, macro data cannot be substituted, so the registry
surfaces the failure instead of silently serving prices in its place.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta

from src.ingestion.base import AdapterError, FetchRequest

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# The three series mandated by spec §3.2.
SUPPORTED_SERIES: tuple[str, ...] = ("GDP", "CPIAUCSL", "FEDFUNDS")

ENDPOINT = "series/observations"

# FRED refuses an initial-release request whose real-time window spans more
# vintage dates than it allows: DGS10 over the full history (realtime_start
# 1776-07-04) is an HTTP 400 "exceeds the maximum number of vintage dates
# allowed". The default window is therefore the last two years; a caller that
# needs more passes its own `realtime_start`.
DEFAULT_WINDOW_DAYS = 730


def _today() -> date:
    """Today's UTC date; a seam for tests."""
    return datetime.now(UTC).date()


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
            # Initial-release vintages: output_type 4 returns each observation
            # as first published within the real-time window. The window is
            # bounded by FRED's vintage-date limit (see DEFAULT_WINDOW_DAYS);
            # a value first released before the window start comes back with
            # its realtime_start clipped to that start.
            "output_type": "4",
            "realtime_start": (_today() - timedelta(days=DEFAULT_WINDOW_DAYS)).isoformat(),
            "realtime_end": "9999-12-31",
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


__all__ = ["BASE_URL", "DEFAULT_WINDOW_DAYS", "ENDPOINT", "FredAdapter", "SUPPORTED_SERIES"]
