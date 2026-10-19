"""Parsers for the raw provider bodies the pipeline reads.

Each parser takes the stored response bytes and returns small frozen records.
Error bodies (non-JSON, or JSON of the wrong shape such as FRED's
``{"Error Message": ...}`` or Tiingo's ``{"detail": ...}``) raise
:class:`ParseError` rather than yielding empty results.

Only ``json`` and ``datetime`` from the standard library are used.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any


class ParseError(ValueError):
    """A provider body was not in the expected shape."""


@dataclass(frozen=True)
class MacroPoint:
    """One macro observation and the day it was first released."""

    observed_on: date
    released_on: date
    value: float


@dataclass(frozen=True)
class PricePoint:
    """One adjusted closing price."""

    trading_day: date
    adj_close: float


@dataclass(frozen=True)
class AssetProfile:
    """Name and currency of a listed asset."""

    name: str
    currency: str


@dataclass(frozen=True)
class NewsItem:
    """One news article."""

    article_id: str
    published_at: datetime
    headline: str
    summary: str
    source: str


def _load(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ParseError(f"body is not valid JSON: {exc}") from exc


def _rows(body: bytes, key: str | None) -> list[Any]:
    data = _load(body)
    if key is not None:
        if not isinstance(data, dict) or not isinstance(data.get(key), list):
            raise ParseError(f"expected an object with a {key!r} list")
        return data[key]
    if not isinstance(data, list):
        raise ParseError("expected a JSON array")
    return data


def parse_fred_observations(body: bytes) -> list[MacroPoint]:
    """Parse FRED observations, dropping missing values and later revisions."""
    earliest: dict[date, MacroPoint] = {}
    try:
        for row in _rows(body, "observations"):
            if row["value"] == ".":
                continue
            observed = date.fromisoformat(row["date"])
            released = date.fromisoformat(row["realtime_start"])
            kept = earliest.get(observed)
            # First release wins, whatever order the vintages arrive in.
            if kept is None or released < kept.released_on:
                earliest[observed] = MacroPoint(observed, released, float(row["value"]))
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ParseError):
            raise
        raise ParseError(f"malformed FRED observation: {exc!r}") from exc
    points = [earliest[d] for d in sorted(earliest)]
    return points


def parse_tiingo_prices(body: bytes) -> list[PricePoint]:
    """Parse Tiingo daily prices into adjusted closes by calendar day."""
    try:
        return [
            PricePoint(date.fromisoformat(row["date"][:10]), float(row["adjClose"]))
            for row in _rows(body, None)
        ]
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ParseError):
            raise
        raise ParseError(f"malformed Tiingo price row: {exc!r}") from exc


def parse_finnhub_profile(body: bytes) -> AssetProfile | None:
    """Parse a Finnhub profile; ``None`` for the empty object of an unknown symbol."""
    data = _load(body)
    if not isinstance(data, dict):
        raise ParseError("expected a JSON object")
    if not data:
        return None
    try:
        return AssetProfile(str(data["name"]), str(data["currency"]))
    except KeyError as exc:
        raise ParseError(f"profile is missing {exc}") from exc


def parse_finnhub_news(body: bytes) -> list[NewsItem]:
    """Parse Finnhub news into items with timezone-aware UTC times."""
    try:
        return [
            NewsItem(
                article_id=str(row["id"]),
                published_at=datetime.fromtimestamp(row["datetime"], UTC),
                headline=row["headline"],
                summary=row["summary"],
                source=row["source"],
            )
            for row in _rows(body, None)
        ]
    except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
        if isinstance(exc, ParseError):
            raise
        raise ParseError(f"malformed Finnhub news row: {exc!r}") from exc


__all__ = [
    "AssetProfile",
    "MacroPoint",
    "NewsItem",
    "ParseError",
    "PricePoint",
    "parse_finnhub_news",
    "parse_finnhub_profile",
    "parse_fred_observations",
    "parse_tiingo_prices",
]
