"""Derive CORRELATES_WITH and INFLUENCES edges from point-in-time series.

Every statistic here is computed only from observations whose ``available_at``
lies inside ``[at - window_days, at]``. Anything published after ``at`` is
invisible, so replaying the same ``at`` always yields the same edges. Edge
timestamps come from the ``at`` argument, never the wall clock.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from itertools import combinations
from zoneinfo import ZoneInfo

from src.graph.align import Observation
from src.graph.schema import GraphEdge, RelationType

_NY = ZoneInfo("America/New_York")
_MARKET_CLOSE = time(16, 0)
_END_OF_DAY = time(23, 59, 59)
_R_CLAMP = 0.999999


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation, or None when n < 3 or either variance is zero."""
    n = len(xs)
    if n != len(ys):
        raise ValueError("pearson requires series of equal length")
    if n < 3:
        return None
    mean_x = math.fsum(xs) / n
    mean_y = math.fsum(ys) / n
    sxx = math.fsum((x - mean_x) ** 2 for x in xs)
    syy = math.fsum((y - mean_y) ** 2 for y in ys)
    if sxx <= 0.0 or syy <= 0.0:
        return None
    sxy = math.fsum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    return _clamp(sxy / math.sqrt(sxx * syy), -1.0, 1.0)


def significance(r: float, n: int) -> float:
    """One minus the two-sided Fisher-z p-value for correlation ``r`` over ``n``."""
    if n < 4:
        return 0.0
    z = math.atanh(_clamp(r, -_R_CLAMP, _R_CLAMP)) * math.sqrt(n - 3)
    p = math.erfc(abs(z) / math.sqrt(2))
    return _clamp(1.0 - p, 0.0, 1.0)


def close_available_at(trading_day: date) -> datetime:
    """UTC moment the 16:00 America/New_York close of ``trading_day`` is known."""
    return datetime.combine(trading_day, _MARKET_CLOSE, tzinfo=_NY).astimezone(UTC)


def release_available_at(released_on: date, release_time: time | None) -> datetime:
    """UTC moment a macro release becomes available.

    ``release_time`` is read in America/New_York. When it is unknown the end of
    the day (23:59:59 New York) is assumed, which is the conservative choice.
    """
    when = release_time if release_time is not None else _END_OF_DAY
    return datetime.combine(released_on, when, tzinfo=_NY).astimezone(UTC)


def _in_window(
    observations: Sequence[Observation], start: datetime, at: datetime
) -> list[Observation]:
    return sorted(
        (o for o in observations if start <= o.available_at <= at),
        key=lambda o: (o.available_at.astimezone(UTC), o.observed_at.astimezone(UTC)),
    )


def _closes_by_day(observations: Sequence[Observation]) -> dict[date, float]:
    """Map trading day to close; the latest-available duplicate wins."""
    closes: dict[date, float] = {}
    for obs in observations:  # already ordered by availability
        value = float(obs.value)  # type: ignore[arg-type]
        if value > 0.0:
            closes[obs.observed_at.astimezone(UTC).date()] = value
    return closes


def _edge(
    src: str,
    dst: str,
    rel: RelationType,
    r: float,
    n: int,
    start: datetime,
    at: datetime,
) -> GraphEdge:
    return GraphEdge(
        src_node=src,
        dst_node=dst,
        rel_type=rel,
        weight=_clamp(r, -1.0, 1.0),
        confidence=significance(r, n),
        valid_from=start,
        valid_to=at,
        observation_count=n,
        computed_at=at,
    )


def correlation_edges(
    prices: Mapping[str, Sequence[Observation]],
    *,
    at: datetime,
    window_days: int,
    min_observations: int = 20,
) -> list[GraphEdge]:
    """CORRELATES_WITH edges between assets from shared-day log returns."""
    start = at - timedelta(days=window_days)
    closes = {
        asset: _closes_by_day(_in_window(series, start, at))
        for asset, series in prices.items()
    }
    edges: list[GraphEdge] = []
    for a, b in combinations(sorted(closes), 2):
        days = sorted(closes[a].keys() & closes[b].keys())
        pairs = list(zip(days, days[1:], strict=False))
        ret_a = [math.log(closes[a][d] / closes[a][p]) for p, d in pairs]
        ret_b = [math.log(closes[b][d] / closes[b][p]) for p, d in pairs]
        n = len(ret_a)
        r = pearson(ret_a, ret_b)
        if r is None or n < min_observations:
            continue
        edges.append(_edge(a, b, RelationType.CORRELATES_WITH, r, n, start, at))
    return edges


def influence_edges(
    macro: Mapping[str, Sequence[Observation]],
    prices: Mapping[str, Sequence[Observation]],
    *,
    at: datetime,
    window_days: int,
    min_releases: int = 8,
) -> list[GraphEdge]:
    """INFLUENCES edges from indicator release changes to the next price move."""
    start = at - timedelta(days=window_days)
    price_series = {
        asset: [
            (o.available_at, float(o.value))  # type: ignore[arg-type]
            for o in _in_window(series, start, at)
        ]
        for asset, series in prices.items()
    }
    edges: list[GraphEdge] = []
    for code in sorted(macro):
        releases = _in_window(macro[code], start, at)
        for asset in sorted(price_series):
            series = price_series[asset]
            deltas: list[float] = []
            returns: list[float] = []
            for prev, cur in zip(releases, releases[1:], strict=False):
                before = None
                after = None
                for avail, value in series:
                    if avail <= cur.available_at:
                        before = value
                    else:
                        after = value
                        break
                if before is None or after is None or before == 0.0:
                    continue
                deltas.append(float(cur.value) - float(prev.value))  # type: ignore[arg-type]
                returns.append(after / before - 1.0)
            n = len(deltas)
            r = pearson(deltas, returns)
            if r is None or n < min_releases:
                continue
            edges.append(_edge(code, asset, RelationType.INFLUENCES, r, n, start, at))
    return edges


__all__ = [
    "close_available_at",
    "correlation_edges",
    "influence_edges",
    "pearson",
    "release_available_at",
    "significance",
]
