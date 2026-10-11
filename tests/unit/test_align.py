"""Point-in-time replay and alignment tests (T054) — FR-014, VR-5.

This is the project's most important correctness rule, so it is tested the most
strictly. The rule: alignment across disparate frequencies uses a **backward
as-of join with a strict no-lookahead invariant** — a data point may only observe
values already published at its own timestamp.

The classic failure is conflating *observation time* (the period a value
describes) with *availability time* (when it became knowable). A CPI print
released on the 12th covering January must not be observable by a graph state on
January 31. Two tests here guard against that specifically.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.graph.align import (
    AlignmentError,
    Observation,
    align,
    first_available_at_or_before,
)

UTC = UTC


def _ts(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


class TestBackwardAsOfJoin:
    """The join must look backwards only."""

    def test_uses_only_past_values(self):
        """Test that uses only past values."""
        observations = [
            Observation("v1", _ts(2026, 1, 1), _ts(2026, 1, 1)),
            Observation("v2", _ts(2026, 2, 1), _ts(2026, 2, 1)),
        ]
        # A query for mid-February must see v2, not anything later.
        result = first_available_at_or_before(observations, _ts(2026, 2, 15))
        assert result is not None
        assert result.value == "v2"

    def test_before_any_value_returns_none(self):
        """Test that before any value returns none."""
        observations = [Observation("v1", _ts(2026, 1, 1), _ts(2026, 6, 1))]
        assert first_available_at_or_before(observations, _ts(2026, 1, 15)) is None

    def test_uses_availability_not_observation_time(self):
        """THE core defect: release lag must not leak a value backwards.

        A value describing January but published on 12 February is not
        observable on 31 January, even though its observation time has passed.
        """
        observations = [
            Observation("jan-cpi", _ts(2026, 1, 1), _ts(2026, 2, 12)),
        ]
        # Querying at end of January: observation time has passed, but the
        # release had not happened yet.
        assert first_available_at_or_before(observations, _ts(2026, 1, 31)) is None
        # After the release, it becomes visible.
        found = first_available_at_or_before(observations, _ts(2026, 2, 15))
        assert found is not None
        assert found.value == "jan-cpi"

    def test_exact_availability_boundary_is_visible(self):
        """Test that exact availability boundary is visible."""
        observations = [Observation("v", _ts(2026, 1, 1), _ts(2026, 3, 1, 12))]
        found = first_available_at_or_before(observations, _ts(2026, 3, 1, 12))
        assert found is not None
        assert found.value == "v"

    def test_picks_the_latest_of_many_past_values(self):
        """Test that picks the latest of many past values."""
        observations = [
            Observation(f"v{i}", _ts(2026, i, 1), _ts(2026, i, 1))
            for i in range(1, 7)
        ]
        found = first_available_at_or_before(observations, _ts(2026, 6, 15))
        assert found.value == "v6"

    def test_empty_series(self):
        """Test that empty series."""
        assert first_available_at_or_before([], _ts(2026, 1, 1)) is None

    def test_naive_datetimes_are_rejected(self):
        """VR-5 cannot be enforced without an unambiguous clock."""
        with pytest.raises((AlignmentError, ValueError)):
            observations = [Observation("v", datetime(2026, 1, 1), datetime(2026, 1, 1))]
            first_available_at_or_before(observations, datetime(2026, 2, 1))


class TestAlign:
    """The multi-series alignment used before graph upsert."""

    def test_preserves_column_order_and_picks_hits(self):
        """Test that preserves column order and picks hits."""
        series = {
            "sentiment": [
                Observation("s1", _ts(2026, 1, 10), _ts(2026, 1, 10)),
                Observation("s2", _ts(2026, 2, 10), _ts(2026, 2, 10)),
            ],
            "macro": [
                # Describes January, published in February.
                Observation("m1", _ts(2026, 1, 1), _ts(2026, 2, 5)),
            ],
        }
        rows = align(series, at=_ts(2026, 2, 10))
        assert rows["sentiment"] == "s2"
        assert rows["macro"] == "m1"

    def test_a_series_with_no_past_value_is_not_invented(self):
        """Test that a series with no past value is not invented."""
        series = {
            "present": [Observation("p", _ts(2026, 1, 1), _ts(2026, 1, 1))],
            "absent": [Observation("a", _ts(2026, 6, 1), _ts(2026, 6, 1))],
        }
        rows = align(series, at=_ts(2026, 1, 15))
        assert rows["present"] == "p"
        assert "absent" not in rows or rows["absent"] is None

    def test_rejects_naive_query_time(self):
        """Test that rejects naive query time."""
        series = {"a": [Observation("x", _ts(2026, 1, 1), _ts(2026, 1, 1))]}
        with pytest.raises((AlignmentError, ValueError)):
            align(series, at=datetime(2026, 2, 1))


class TestObservation:
    """Test observation."""
    def test_ordering_is_by_availability(self):
        """Test that ordering is by availability."""
        a = Observation("a", _ts(2026, 1, 1), _ts(2026, 2, 1))
        b = Observation("b", _ts(2026, 1, 1), _ts(2026, 1, 15))
        assert b < a, "an earlier availability must sort first"


class TestReplayDeterminism:
    """A fixed input set must produce a byte-identical aligned result."""

    def test_aligning_twice_gives_identical_rows(self):
        """Test that aligning twice gives identical rows."""
        series = {
            "sentiment": [
                Observation(f"s{i}", _ts(2026, i, 5), _ts(2026, i, 5))
                for i in range(1, 6)
            ],
            "macro": [
                Observation("m", _ts(2026, 1, 1), _ts(2026, 3, 20)),
            ],
        }
        first = align(series, at=_ts(2026, 5, 1))
        second = align(series, at=_ts(2026, 5, 1))
        assert first == second

    def test_release_lag_is_survivable(self):
        """A release months later must not silently backdate."""
        series = {
            "slow": [Observation("slow", _ts(2026, 1, 1), _ts(2026, 11, 1))],
        }
        for month in (2, 5, 9):
            rows = align(series, at=_ts(2026, month, 28))
            assert "slow" not in rows, (
                f"a value released in November leaked into month {month}"
            )

    def test_sentiment_lag_is_relative(self):
        """High-frequency sentiment must not wait for slow macro releases."""
        series = {
            "sentiment": [Observation("s", _ts(2026, 1, 2), _ts(2026, 1, 2))],
            "macro": [Observation("m", _ts(2026, 1, 1), _ts(2026, 10, 1))],
        }
        rows = align(series, at=_ts(2026, 6, 1))
        assert rows["sentiment"] == "s"
        assert "macro" not in rows

    def test_output_depends_only_on_inputs(self):
        """No clock or randomness may enter the result."""
        series = {
            "a": [Observation("a", _ts(2026, 1, 1), _ts(2026, 1, 1))],
        }
        assert align(series, at=_ts(2026, 2, 1)) == align(series, at=_ts(2026, 2, 1))
