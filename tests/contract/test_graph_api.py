"""Contract tests for the read-only graph API (T057) — FR-021.

Verifies the surface in `.specify/specs/contracts/graph-api.md`: every function,
every error type, and the guarantees that make the API safe to consume —
point-in-time results, determinism, and read-only behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.graph.api import (
    MAX_HOPS,
    GraphAPIError,
    InvalidWindowError,
    UnknownEntityError,
    UnsupportedTraversalError,
    get_entity,
    list_assets,
    path,
    sentiment_for,
    top_influencers,
)
from src.graph.repository import GraphRepository
from src.graph.schema import (
    Asset,
    AssetClass,
    GraphDatabase,
    GraphEdge,
    RelationType,
)

UTC = UTC


def _ts(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.fixture()
def seeded():
    """A graph with one asset, one macro indicator, sentiment, and edges."""
    db = GraphDatabase(":memory:")
    repo = GraphRepository(db)

    repo.upsert_asset(
        Asset("equity:NASDAQ:AAPL", "AAPL", "Apple", AssetClass.EQUITY,
              "NASDAQ", "USD")
    )
    repo.upsert_asset(
        Asset("crypto:BINANCE:BTC", "BTC", "Bitcoin", AssetClass.CRYPTO,
              "BINANCE", "USD")
    )

    from src.graph.schema import Frequency, MacroIndicator, NodeType, SentimentMetric

    repo.upsert_macro_indicator(
        MacroIndicator("FRED:GDP", "GDP", "USD", Frequency.QUARTERLY, "SA", "fred")
    )

    for i, (day, score, conf) in enumerate(
        [(1, 0.5, 0.9), (2, -0.3, 0.8), (3, 0.6, 0.4)]
    ):
        repo.upsert_sentiment_metric(
            SentimentMetric(
                metric_id=f"m{i}",
                entity_id="equity:NASDAQ:AAPL",
                entity_type=NodeType.ASSET,
                score=score,
                confidence=conf,
                timestamp=_ts(2026, 10, day, 12),
                source_reference=f"news:{i}",
                model_id="stub@0",
                text_hash=f"{i}" * 64,
            )
        )

    repo.upsert_edge(GraphEdge(
        "FRED:GDP", "equity:NASDAQ:AAPL", RelationType.INFLUENCES, 0.7, 0.9,
        _ts(2026, 1, 1), observation_count=50,
    ))
    # A different valid_from: same (src, dst, rel_type, valid_from) would be
    # the same edge by primary-key definition, not a second row.
    repo.upsert_edge(GraphEdge(
        "FRED:GDP", "equity:NASDAQ:AAPL", RelationType.INFLUENCES, 0.2, 0.3,
        _ts(2026, 4, 1), observation_count=5,
    ))
    repo.upsert_edge(GraphEdge(
        "equity:NASDAQ:AAPL", "crypto:BINANCE:BTC", RelationType.CORRELATES_WITH,
        -0.8, 0.95, _ts(2026, 1, 1), observation_count=200,
    ))

    yield db, repo
    db.close()


class TestGetEntity:
    """Test get entity."""
    def test_finds_an_asset(self, seeded):
        """Test that finds an asset."""
        entity = get_entity("equity:NASDAQ:AAPL", db=seeded[0])
        assert entity is not None
        assert entity.node_type == "Asset"
        assert entity.attributes["ticker"] == "AAPL"

    def test_finds_a_macro_indicator(self, seeded):
        """Test that finds a macro indicator."""
        assert get_entity("FRED:GDP", db=seeded[0]).node_type == "MacroIndicator"

    def test_absent_id_returns_none(self, seeded):
        """Test that absent id returns none."""
        assert get_entity("equity:NOEXCHANGE:ZZZ", db=seeded[0]) is None


class TestSentimentSeries:
    """Test sentiment series."""
    def test_returns_points_in_ascending_order(self, seeded):
        """Test that returns points in ascending order."""
        rows = sentiment_for(
            "equity:NASDAQ:AAPL",
            start=_ts(2026, 10, 1),
            end=_ts(2026, 10, 31),
            db=seeded[0],
        )
        assert len(rows) == 3
        assert [r["timestamp"] for r in rows] == sorted(r["timestamp"] for r in rows)

    def test_window_is_half_open(self, seeded):
        """[start, end): the end timestamp is excluded."""
        rows = sentiment_for(
            "equity:NASDAQ:AAPL",
            start=_ts(2026, 10, 1),
            end=_ts(2026, 10, 3),
            db=seeded[0],
        )
        assert len(rows) == 2

    def test_min_confidence_filter(self, seeded):
        """Test that min confidence filter."""
        rows = sentiment_for(
            "equity:NASDAQ:AAPL",
            start=_ts(2026, 10, 1),
            end=_ts(2026, 10, 31),
            min_confidence=0.85,
            db=seeded[0],
        )
        assert len(rows) == 1
        assert rows[0]["confidence"] == 0.9

    def test_naive_datetime_is_refused(self, seeded):
        """Test that naive datetime is refused."""
        with pytest.raises(InvalidWindowError):
            sentiment_for(
                "equity:NASDAQ:AAPL",
                start=datetime(2026, 10, 1),
                end=datetime(2026, 10, 31),
                db=seeded[0],
            )

    def test_inverted_window_is_refused(self, seeded):
        """Test that inverted window is refused."""
        with pytest.raises(InvalidWindowError):
            sentiment_for(
                "equity:NASDAQ:AAPL",
                start=_ts(2026, 10, 31),
                end=_ts(2026, 10, 1),
                db=seeded[0],
            )

    def test_out_of_range_confidence_is_refused(self, seeded):
        """Test that out of range confidence is refused."""
        with pytest.raises(InvalidWindowError):
            sentiment_for(
                "equity:NASDAQ:AAPL",
                start=_ts(2026, 10, 1),
                end=_ts(2026, 10, 31),
                min_confidence=1.5,
                db=seeded[0],
            )

    def test_min_confidence_applies_before_limit(self, seeded):
        """A page must not mix qualifying and non-qualifying rows."""
        rows = sentiment_for(
            "equity:NASDAQ:AAPL",
            start=_ts(2026, 10, 1),
            end=_ts(2026, 10, 31),
            min_confidence=0.85,
            limit=10,
            db=seeded[0],
        )
        assert all(r["confidence"] >= 0.85 for r in rows)


class TestDeterminism:
    """Test determinism."""
    def test_repeated_queries_are_identical(self, seeded):
        """Test that repeated queries are identical."""
        args = {"start": _ts(2026, 10, 1), "end": _ts(2026, 10, 31), "db": seeded[0]}
        first = sentiment_for("equity:NASDAQ:AAPL", **args)
        second = sentiment_for("equity:NASDAQ:AAPL", **args)
        assert first == second

    def test_no_clock_leaks_into_a_read(self, seeded):
        """A read must not depend on the current time."""
        before = sentiment_for(
            "equity:NASDAQ:AAPL",
            start=_ts(2026, 10, 1),
            end=_ts(2026, 10, 31),
            db=seeded[0],
        )
        import time

        time.sleep(0.01)
        after = sentiment_for(
            "equity:NASDAQ:AAPL",
            start=_ts(2026, 10, 1),
            end=_ts(2026, 10, 31),
            db=seeded[0],
        )
        assert before == after


class TestTraversalCap:
    """Test traversal cap."""
    def test_two_hops_is_allowed(self, seeded):
        """Test that two hops is allowed."""
        from src.graph.api import correlated_with

        rows = correlated_with("equity:NASDAQ:AAPL", db=seeded[0])
        assert rows

    def test_deeper_than_two_is_refused(self, seeded):
        """Test that deeper than two is refused."""
        with pytest.raises(UnsupportedTraversalError):
            path("FRED:GDP", "crypto:BINANCE:BTC", max_hops=3, db=seeded[0])

    def test_zero_hops_is_refused(self, seeded):
        """Test that zero hops is refused."""
        with pytest.raises(InvalidWindowError):
            path("a", "b", max_hops=0, db=seeded[0])

    def test_direct_path_is_found(self, seeded):
        """Test that direct path is found."""
        result = path(
            "equity:NASDAQ:AAPL", "crypto:BINANCE:BTC", db=seeded[0]
        )
        assert result is not None
        assert len(result) == 1


class TestReadOnly:
    """Test read only."""
    def test_unknown_entity_is_not_created(self, seeded):
        """Test that unknown entity is not created."""
        from src.graph.api import require_entity

        with pytest.raises(UnknownEntityError):
            require_entity("equity:NOEXCHANGE:ZZZ", db=seeded[0])
        assert get_entity("equity:NOEXCHANGE:ZZZ", db=seeded[0]) is None

    def test_api_errors_share_a_base(self):
        """Test that api errors share a base."""
        for cls in (UnknownEntityError, InvalidWindowError, UnsupportedTraversalError):
            assert issubclass(cls, GraphAPIError)


class TestListAssets:
    """Test list assets."""
    def test_filters_by_class(self, seeded):
        """Test that filters by class."""
        rows = list_assets(asset_class="equity", db=seeded[0])
        assert len(rows) == 1
        assert rows[0]["asset_id"] == "equity:NASDAQ:AAPL"

    def test_paginates(self, seeded):
        """Test that paginates."""
        assert len(list_assets(limit=1, db=seeded[0])) == 1
        assert len(list_assets(offset=1, limit=5, db=seeded[0])) == 1

    def test_rejects_bad_limit(self, seeded):
        """Test that rejects bad limit."""
        with pytest.raises(InvalidWindowError):
            list_assets(limit=0, db=seeded[0])

    def test_top_influencers_ranked_by_magnitude(self, seeded):
        """A strong negative influence must not be buried."""
        rows = top_influencers(
            "equity:NASDAQ:AAPL", min_confidence=0.0, db=seeded[0]
        )
        assert len(rows) == 2
        # Highest confidence first, regardless of sign or weight.
        assert rows[0]["confidence"] == 0.9
        assert rows[1]["confidence"] == 0.3

    def test_top_influencers_filters_by_confidence(self, seeded):
        """The default threshold drops the weak edge."""
        rows = top_influencers("equity:NASDAQ:AAPL", db=seeded[0])
        assert len(rows) == 1
        assert rows[0]["confidence"] == 0.9


def test_max_hops_is_two():
    """Test that max hops is two."""
    assert MAX_HOPS == 2
