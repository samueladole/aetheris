"""Tests for deriving CORRELATES_WITH and INFLUENCES edges under no-lookahead."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.graph.align import AlignmentError, Observation
from src.graph.edges import (
    close_available_at,
    correlation_edges,
    influence_edges,
    pearson,
    release_available_at,
    significance,
)
from src.graph.schema import RelationType

NY = ZoneInfo("America/New_York")
START = date(2026, 1, 5)
AT = datetime(2026, 12, 31, tzinfo=UTC)


def _day(i: int) -> date:
    return START + timedelta(days=i)


def _prices(closes: list[float]) -> list[Observation]:
    return [
        Observation(
            value=c,
            observed_at=datetime(_day(i).year, _day(i).month, _day(i).day, tzinfo=UTC),
            available_at=close_available_at(_day(i)),
        )
        for i, c in enumerate(closes)
    ]


def _wavy(n: int, phase: float = 0.0) -> list[float]:
    return [100.0 * math.exp(0.05 * math.sin(0.9 * i + phase) + 0.001 * i) for i in range(n)]


def _macro(values: list[float], first_day: int = 1) -> list[Observation]:
    out = []
    for i, v in enumerate(values):
        d = _day(first_day + i)
        released = datetime(d.year, d.month, d.day, 12, tzinfo=UTC)
        out.append(Observation(value=v, observed_at=released, available_at=released))
    return out


def test_pearson_edge_cases() -> None:
    """Pearson returns None for short or constant series and 1.0 for a line."""
    assert pearson([1.0, 2.0], [1.0, 2.0]) is None
    assert pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
    assert pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)
    assert pearson([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == pytest.approx(-1.0)


def test_significance_bounds() -> None:
    """Significance is 0 for no correlation or tiny n, and near 1 for strong r."""
    assert significance(0.0, 100) == pytest.approx(0.0, abs=1e-9)
    assert significance(0.9, 100) > 0.999
    assert significance(0.9, 3) == 0.0
    assert 0.0 <= significance(1.0, 50) <= 1.0
    assert 0.0 <= significance(-5.0, 50) <= 1.0


def test_close_available_at_across_dst() -> None:
    """The 16:00 New York close is 21:00 UTC in winter and 20:00 UTC in summer."""
    assert close_available_at(date(2026, 1, 15)) == datetime(2026, 1, 15, 21, tzinfo=UTC)
    assert close_available_at(date(2026, 7, 15)) == datetime(2026, 7, 15, 20, tzinfo=UTC)


def test_release_available_at() -> None:
    """Macro releases use their stated New York time, else end of day."""
    assert release_available_at(date(2026, 2, 13), time(8, 30)) == datetime(
        2026, 2, 13, 13, 30, tzinfo=UTC
    )
    assert release_available_at(date(2026, 7, 14), time(8, 30)) == datetime(
        2026, 7, 14, 12, 30, tzinfo=UTC
    )
    assert release_available_at(date(2026, 2, 13), None) == datetime(
        2026, 2, 14, 4, 59, 59, tzinfo=UTC
    )
    assert release_available_at(date(2026, 7, 14), None) == datetime(
        2026, 7, 15, 3, 59, 59, tzinfo=UTC
    )


def test_identical_series_correlate_fully() -> None:
    """Identical series give weight 1, high confidence and n of closes minus one."""
    closes = _wavy(30)
    edges = correlation_edges(
        {"A": _prices(closes), "B": _prices(closes)}, at=AT, window_days=730
    )
    assert len(edges) == 1
    e = edges[0]
    assert e.rel_type is RelationType.CORRELATES_WITH
    assert e.weight == pytest.approx(1.0)
    assert e.weight <= 1.0
    assert e.confidence > 0.99
    assert e.observation_count == 29
    assert e.valid_from == AT - timedelta(days=730)
    assert e.valid_to == AT
    assert e.computed_at == AT


def test_canonical_ordering() -> None:
    """Every edge has src_node sorted before dst_node regardless of dict order."""
    prices = {
        "C": _prices(_wavy(30, 0.0)),
        "A": _prices(_wavy(30, 1.0)),
        "B": _prices(_wavy(30, 2.0)),
    }
    edges = correlation_edges(prices, at=AT, window_days=730)
    assert len(edges) == 3
    assert all(e.src_node < e.dst_node for e in edges)


def test_too_few_observations_emit_nothing() -> None:
    """Ten closes are below the default minimum of twenty observations."""
    prices = {"A": _prices(_wavy(10)), "B": _prices(_wavy(10, 1.0))}
    assert correlation_edges(prices, at=AT, window_days=730) == []


def test_close_after_at_is_not_seen() -> None:
    """A close at 16:00 New York is not available at 15:59 New York."""
    closes = _wavy(30)
    at = datetime(_day(29).year, _day(29).month, _day(29).day, 15, 59, tzinfo=NY)
    edges = correlation_edges(
        {"A": _prices(closes), "B": _prices(closes)}, at=at, window_days=730
    )
    assert len(edges) == 1
    assert edges[0].observation_count == 28
    assert edges[0].computed_at == at


def test_window_excludes_old_closes() -> None:
    """Closes older than the window are dropped before returns are formed."""
    closes = _wavy(30)
    at = close_available_at(_day(29))
    edges = correlation_edges(
        {"A": _prices(closes), "B": _prices(closes)},
        at=at,
        window_days=24,
        min_observations=5,
    )
    assert len(edges) == 1
    # Closes whose availability is before at - 24 days are excluded.
    assert edges[0].observation_count < 29


def test_constant_series_emits_nothing() -> None:
    """A zero-variance return series has no correlation and emits no edge."""
    prices = {"A": _prices([100.0] * 30), "B": _prices(_wavy(30))}
    assert correlation_edges(prices, at=AT, window_days=730) == []


def test_inverse_influence_is_negative() -> None:
    """A rising indicator with falling returns on release gives a negative weight."""
    d = [1, 2, 1, 3, 2, 1, 3, 2, 1, 2]
    level = 50.0
    values = [level]
    closes = [100.0]
    for step in d:
        level += step
        values.append(level)
        closes.append(closes[-1] * (1 - 0.01 * step))
    # Release j is on day j at 12:00 UTC; close j follows it that day.
    edges = influence_edges(
        {"CPI": _macro(values, first_day=0)},
        {"AAA": _prices(closes)},
        at=AT,
        window_days=730,
    )
    assert len(edges) == 1
    e = edges[0]
    assert e.rel_type is RelationType.INFLUENCES
    assert (e.src_node, e.dst_node) == ("CPI", "AAA")
    assert e.weight < 0
    assert e.weight >= -1.0
    assert e.observation_count == 10
    assert e.computed_at == AT
    assert e.valid_from == AT - timedelta(days=730)


def _influence_fixture(n_releases: int) -> tuple[dict, dict]:
    values = [50.0 + (i * i) % 7 for i in range(n_releases)]
    closes = _wavy(n_releases + 5)
    return {"CPI": _macro(values)}, {"AAA": _prices(closes)}


def test_release_without_a_later_close_is_skipped() -> None:
    """A release after the final close contributes nothing and raises nothing."""
    macro, prices = _influence_fixture(12)
    late = datetime(2026, 6, 1, 12, tzinfo=UTC)
    macro["CPI"] = [*macro["CPI"], Observation(60.0, late, late)]
    with_late = influence_edges(macro, prices, at=AT, window_days=730)
    macro_no_late, _ = _influence_fixture(12)
    without = influence_edges(macro_no_late, prices, at=AT, window_days=730)
    assert with_late == without


def test_release_before_first_close_is_skipped() -> None:
    """A release with no earlier close contributes no pair, and raises nothing."""
    macro, prices = _influence_fixture(12)
    base = influence_edges(macro, prices, at=AT, window_days=730)[0]
    e1 = datetime(2025, 12, 1, 12, tzinfo=UTC)
    e2 = datetime(2025, 12, 2, 12, tzinfo=UTC)
    macro["CPI"] = [Observation(40.0, e1, e1), Observation(41.0, e2, e2), *macro["CPI"]]
    edges = influence_edges(macro, prices, at=AT, window_days=730)
    # The e2 release has no earlier close (skipped); the first real release adds one.
    assert edges[0].observation_count == base.observation_count + 1


def test_fewer_than_min_releases_emit_nothing() -> None:
    """Seven usable releases are below the default minimum of eight."""
    macro, prices = _influence_fixture(8)  # 8 releases -> 7 deltas
    assert influence_edges(macro, prices, at=AT, window_days=730) == []


def test_release_after_at_is_not_seen() -> None:
    """Releases and closes after the as-of time never contribute."""
    macro, prices = _influence_fixture(14)
    at = datetime(_day(10).year, _day(10).month, _day(10).day, 12, tzinfo=UTC)
    edges = influence_edges(macro, prices, at=at, window_days=730, min_releases=2)
    assert len(edges) == 1
    assert edges[0].observation_count == 8


def test_edges_are_deterministic() -> None:
    """Equal inputs give equal outputs."""
    macro, prices = _influence_fixture(14)
    assert influence_edges(macro, prices, at=AT, window_days=730) == influence_edges(
        macro, prices, at=AT, window_days=730
    )


def test_naive_at_is_rejected() -> None:
    """A timezone-naive as-of time raises AlignmentError even with empty inputs."""
    naive = datetime(2026, 12, 31)
    with pytest.raises(AlignmentError):
        correlation_edges({}, at=naive, window_days=730)
    with pytest.raises(AlignmentError):
        influence_edges({}, {}, at=naive, window_days=730)


def test_edges_carry_utc_times() -> None:
    """A New York as-of time is converted so edge datetimes have a zero offset."""
    closes = _wavy(30)
    at = datetime(2026, 12, 30, 12, tzinfo=NY)
    edges = correlation_edges(
        {"A": _prices(closes), "B": _prices(closes)}, at=at, window_days=730
    )
    assert edges[0].computed_at == at
    for stamp in (edges[0].computed_at, edges[0].valid_from, edges[0].valid_to):
        assert stamp is not None
        assert stamp.utcoffset() == timedelta(0)
