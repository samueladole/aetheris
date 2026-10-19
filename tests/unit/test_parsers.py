"""Tests for the provider response parsers."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from src.ingestion.parsers import (
    AssetProfile,
    MacroPoint,
    ParseError,
    PricePoint,
    parse_finnhub_news,
    parse_finnhub_profile,
    parse_fred_observations,
    parse_tiingo_prices,
)

_DIR = Path(__file__).parent.parent / "fixtures" / "providers"


def FIXTURE(name: str) -> bytes:  # noqa: N802
    """Read a provider fixture."""
    return (_DIR / name).read_bytes()


def test_fred_released_on_is_realtime_start():
    """First observation carries its first-release date."""
    pts = parse_fred_observations(FIXTURE("fred_cpiaucsl.json"))
    assert pts[0] == MacroPoint(date(2026, 1, 1), date(2026, 2, 13), 326.588)


def test_fred_missing_values_and_repeats_are_dropped():
    """Missing values drop and the first release wins."""
    body = (
        b'{"observations": [{"realtime_start":"2026-02-13","date":"2026-01-01","value":"."},'
        b'{"realtime_start":"2026-02-13","date":"2026-01-01","value":"1.0"},'
        b'{"realtime_start":"2026-02-14","date":"2026-01-01","value":"2.0"}]}'
    )
    assert parse_fred_observations(body) == [
        MacroPoint(date(2026, 1, 1), date(2026, 2, 13), 1.0)
    ]


def test_fred_first_release_wins_regardless_of_row_order():
    """The earliest vintage wins even when a later one is listed first."""
    body = (
        b'{"observations": [{"realtime_start":"2026-02-14","date":"2026-01-01","value":"2.0"},'
        b'{"realtime_start":"2026-02-13","date":"2026-01-01","value":"1.0"}]}'
    )
    assert parse_fred_observations(body) == [
        MacroPoint(date(2026, 1, 1), date(2026, 2, 13), 1.0)
    ]


def test_tiingo_uses_adjusted_close():
    """Tiingo prices use adjClose and the calendar day."""
    pts = parse_tiingo_prices(FIXTURE("tiingo_aapl_prices.json"))
    assert pts[0] == PricePoint(date(2026, 9, 1), 325.13)  # close is 650.26 in the fixture


def test_profile_parses_name_and_currency():
    """Finnhub profile yields name and currency."""
    prof = parse_finnhub_profile(FIXTURE("finnhub_profile_aapl.json"))
    assert prof == AssetProfile("Apple Inc", "USD")


def test_news_time_is_utc():
    """News timestamps are timezone-aware UTC."""
    items = parse_finnhub_news(FIXTURE("finnhub_news_general.json"))
    assert len(items) == 3
    assert items[0].published_at == datetime.fromtimestamp(1791885473, UTC)
    assert items[0].article_id == "8614107"
    assert items[0].source == "CNBC"


def test_unknown_profile_is_none():
    """An empty profile object means an unknown symbol."""
    assert parse_finnhub_profile(b"{}") is None


@pytest.mark.parametrize(
    "body", [b"not json", b'{"Error Message": "Legacy Endpoint"}', b'{"detail": "Not found."}']
)
def test_error_bodies_raise_parse_error(body):
    """Every parser rejects error bodies."""
    for parser in (
        parse_fred_observations,
        parse_tiingo_prices,
        parse_finnhub_profile,
        parse_finnhub_news,
    ):
        with pytest.raises(ParseError):
            parser(body)
