"""Unit tests for the pipeline watchlist configuration."""

from __future__ import annotations

from datetime import time

import pytest

from src.config.settings import ConfigError
from src.config.watchlist import DEFAULT_WATCHLIST, load_watchlist


def test_default_watchlist_shape():
    """The default watchlist has eight assets and five macro series."""
    assert len(DEFAULT_WATCHLIST.assets) == 8
    assert [s.series_id for s in DEFAULT_WATCHLIST.macro] == [
        "GDP",
        "CPIAUCSL",
        "UNRATE",
        "FEDFUNDS",
        "DGS10",
    ]
    assert DEFAULT_WATCHLIST.macro[1].release_time == time(8, 30)
    assert DEFAULT_WATCHLIST.macro[3].release_time is None
    assert DEFAULT_WATCHLIST.macro[1].indicator_code == "FRED:CPIAUCSL"


def test_missing_config_uses_defaults():
    """No config path falls back to the defaults."""
    assert load_watchlist(None) == DEFAULT_WATCHLIST


def test_config_without_watchlist_table_uses_defaults(tmp_path):
    """A config file with no watchlist table keeps the defaults."""
    path = tmp_path / "a.toml"
    path.write_text("[other]\nx = 1\n")
    assert load_watchlist(str(path)) == DEFAULT_WATCHLIST


def test_toml_watchlist_overrides(tmp_path):
    """TOML entries override the matching defaults only."""
    path = tmp_path / "a.toml"
    path.write_text(
        "[watchlist]\ncorrelation_window_days = 60\n"
        '[[watchlist.assets]]\nasset_id = "equity:NYSE:KO"\nticker = "KO"\n'
        'names = ["Coca-Cola"]\n'
    )
    wl = load_watchlist(str(path))
    assert [a.ticker for a in wl.assets] == ["KO"]
    assert wl.correlation_window_days == 60
    assert wl.macro == DEFAULT_WATCHLIST.macro  # an omitted table keeps its default


def test_toml_macro_series_with_release_time(tmp_path):
    """Macro series parse, including an HH:MM release time."""
    path = tmp_path / "a.toml"
    path.write_text(
        '[[watchlist.macro]]\nseries_id = "PAYEMS"\nname = "Payrolls"\n'
        'unit = "thousands"\nfrequency = "monthly"\nseasonal_adjustment = "SA"\n'
        'release_time = "08:30"\n'
    )
    wl = load_watchlist(str(path))
    assert wl.macro[0].release_time == time(8, 30)
    assert wl.assets == DEFAULT_WATCHLIST.assets


@pytest.mark.parametrize("bad", ["AAPL", "stock:NASDAQ:AAPL", "equity:NASDAQ:"])
def test_invalid_asset_id_is_rejected(tmp_path, bad):
    """Malformed asset ids raise ConfigError."""
    path = tmp_path / "a.toml"
    path.write_text(f'[[watchlist.assets]]\nasset_id = "{bad}"\nticker = "X"\nnames = ["X"]\n')
    with pytest.raises(ConfigError):
        load_watchlist(str(path))


def test_invalid_release_time_is_rejected(tmp_path):
    """A release time not in HH:MM form raises ConfigError."""
    path = tmp_path / "a.toml"
    path.write_text(
        '[[watchlist.macro]]\nseries_id = "X"\nname = "X"\nunit = "u"\n'
        'frequency = "monthly"\nseasonal_adjustment = "SA"\nrelease_time = "8.30"\n'
    )
    with pytest.raises(ConfigError):
        load_watchlist(str(path))
