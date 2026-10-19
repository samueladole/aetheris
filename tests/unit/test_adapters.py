"""Tests for FRED and Tiingo request shapes."""

from src.ingestion.fred_client import FredAdapter
from src.ingestion.market_client import MarketAdapter


def test_fred_requests_initial_release_dates():
    """FRED requests first-release observations by default."""
    req = FredAdapter(api_key="k").series_request("GDP")
    assert (req.params["output_type"], req.params["realtime_start"]) == ("4", "1776-07-04")
    assert req.params["realtime_end"] == "9999-12-31"


def test_fred_caller_params_override_defaults():
    """Caller parameters override the realtime defaults."""
    req = FredAdapter(api_key="k").series_request("GDP", output_type="1")
    assert req.params["output_type"] == "1"


def test_tiingo_prices_path():
    """Tiingo daily requests hit the prices endpoint."""
    req = MarketAdapter("tiingo", api_key="k").build_request(
        "tiingo/daily", {"tickers": "AAPL", "startDate": "2024-10-19"}
    )
    assert req.url == "https://api.tiingo.com/tiingo/daily/AAPL/prices"
    assert req.params["startDate"] == "2024-10-19"
