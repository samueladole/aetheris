"""The pipeline watchlist: what the durable pipeline fetches and scores.

The defaults below are used unless the TOML config file (the one
``Settings.load`` reads) carries a ``[watchlist]`` table. Omitted keys keep
their defaults.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any

from src.config.settings import ConfigError, _env

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

_ASSET_ID_RE = re.compile(r"^(equity|forex|crypto|index):[A-Z0-9.]+:[A-Z0-9.\-]+$")
_RELEASE_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class WatchedAsset:
    """A tradable asset the pipeline fetches prices and news for."""

    asset_id: str
    ticker: str
    names: tuple[str, ...]


@dataclass(frozen=True)
class WatchedSeries:
    """A FRED macro series the pipeline ingests."""

    series_id: str
    name: str
    unit: str
    frequency: str
    seasonal_adjustment: str
    release_time: time | None

    @property
    def indicator_code(self) -> str:
        """Return the graph indicator code for this series."""
        return f"FRED:{self.series_id}"


@dataclass(frozen=True)
class Watchlist:
    """Everything the pipeline watches, plus its derivation windows."""

    assets: tuple[WatchedAsset, ...]
    macro: tuple[WatchedSeries, ...]
    price_history_days: int = 730
    correlation_window_days: int = 126
    influence_window_days: int = 730


_T0830 = time(8, 30)

DEFAULT_WATCHLIST = Watchlist(
    assets=(
        WatchedAsset("equity:NASDAQ:AAPL", "AAPL", ("Apple",)),
        WatchedAsset("equity:NASDAQ:MSFT", "MSFT", ("Microsoft",)),
        WatchedAsset("equity:NASDAQ:NVDA", "NVDA", ("Nvidia",)),
        WatchedAsset("equity:NASDAQ:AMZN", "AMZN", ("Amazon",)),
        WatchedAsset("equity:NYSE:JPM", "JPM", ("JPMorgan",)),
        WatchedAsset("equity:NYSE:XOM", "XOM", ("Exxon",)),
        WatchedAsset("equity:NYSE:JNJ", "JNJ", ("Johnson & Johnson",)),
        WatchedAsset("equity:NYSE:KO", "KO", ("Coca-Cola",)),
    ),
    macro=(
        WatchedSeries("GDP", "Gross Domestic Product", "USD billions", "quarterly", "SA", _T0830),
        WatchedSeries(
            "CPIAUCSL",
            "Consumer Price Index for All Urban Consumers",
            "index",
            "monthly",
            "SA",
            _T0830,
        ),
        WatchedSeries("UNRATE", "Unemployment Rate", "percent", "monthly", "SA", _T0830),
        WatchedSeries(
            "FEDFUNDS", "Federal Funds Effective Rate", "percent", "monthly", "NSA", None
        ),
        WatchedSeries(
            "DGS10", "10-Year Treasury Constant Maturity Rate", "percent", "daily", "NSA", None
        ),
    ),
)


def _require_str(entry: dict[str, Any], key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: {key} must be a non-empty string")
    return value


def _parse_asset(entry: Any, index: int) -> WatchedAsset:
    where = f"watchlist.assets[{index}]"
    if not isinstance(entry, dict):
        raise ConfigError(f"{where} must be a table")
    asset_id = _require_str(entry, "asset_id", where)
    if not _ASSET_ID_RE.match(asset_id):
        raise ConfigError(f"{where}: invalid asset_id {asset_id!r}")
    ticker = _require_str(entry, "ticker", where)
    names = entry.get("names", [])
    if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
        raise ConfigError(f"{where}: names must be a list of non-empty strings")
    return WatchedAsset(asset_id, ticker, tuple(names))


def _parse_release_time(value: Any, where: str) -> time | None:
    if value is None:
        return None
    match = _RELEASE_TIME_RE.match(value) if isinstance(value, str) else None
    if match is None:
        raise ConfigError(f"{where}: release_time must be 'HH:MM', got {value!r}")
    return time(int(match.group(1)), int(match.group(2)))


def _parse_series(entry: Any, index: int) -> WatchedSeries:
    where = f"watchlist.macro[{index}]"
    if not isinstance(entry, dict):
        raise ConfigError(f"{where} must be a table")
    return WatchedSeries(
        series_id=_require_str(entry, "series_id", where),
        name=_require_str(entry, "name", where),
        unit=_require_str(entry, "unit", where),
        frequency=_require_str(entry, "frequency", where),
        seasonal_adjustment=_require_str(entry, "seasonal_adjustment", where),
        release_time=_parse_release_time(entry.get("release_time"), where),
    )


def _parse_window(table: dict[str, Any], key: str, default: int) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"watchlist.{key} must be a positive integer, got {value!r}")
    return value


def load_watchlist(config_path: str | None) -> Watchlist:
    """Load the ``[watchlist]`` table from the config file, else the defaults."""
    path = config_path or _env("AETHERIS_CONFIG")
    if not path:
        return DEFAULT_WATCHLIST
    if not Path(path).is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    table = data.get("watchlist")
    if table is None:
        return DEFAULT_WATCHLIST
    if not isinstance(table, dict):
        raise ConfigError("watchlist must be a table")

    assets = DEFAULT_WATCHLIST.assets
    if "assets" in table:
        raw = table["assets"]
        if not isinstance(raw, list):
            raise ConfigError("watchlist.assets must be an array of tables")
        assets = tuple(_parse_asset(e, i) for i, e in enumerate(raw))
    macro = DEFAULT_WATCHLIST.macro
    if "macro" in table:
        raw = table["macro"]
        if not isinstance(raw, list):
            raise ConfigError("watchlist.macro must be an array of tables")
        macro = tuple(_parse_series(e, i) for i, e in enumerate(raw))

    return Watchlist(
        assets=assets,
        macro=macro,
        price_history_days=_parse_window(table, "price_history_days", 730),
        correlation_window_days=_parse_window(table, "correlation_window_days", 126),
        influence_window_days=_parse_window(table, "influence_window_days", 730),
    )
