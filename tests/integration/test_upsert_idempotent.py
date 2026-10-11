"""Idempotent upsert tests (T055) — FR-013, VR-6.

Idempotency is what makes a re-run safe: upserting the same
``(key, window)`` twice must produce one row. The tests here assert that against
the **database layer**, not an application cache, because only the database can
prove the primary key does the work.
"""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb
import pytest

from src.graph.repository import GraphRepository
from src.graph.schema import (
    Asset,
    AssetClass,
    Frequency,
    GraphDatabase,
    GraphEdge,
    MacroIndicator,
    NodeType,
    RelationType,
    SentimentMetric,
)

UTC = UTC


def _ts(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.fixture()
def repo():
    """Test that repo."""
    db = GraphDatabase(":memory:")
    yield GraphRepository(db)
    db.close()


def _asset() -> Asset:
    return Asset(
        asset_id="equity:NASDAQ:AAPL",
        ticker="AAPL",
        name="Apple Corp",
        asset_class=AssetClass.EQUITY,
        exchange="NASDAQ",
        currency="USD",
    )


def _macro() -> MacroIndicator:
    return MacroIndicator(
        indicator_code="FRED:GDP",
        name="Gross Domestic Product",
        unit="USD",
        frequency=Frequency.QUARTERLY,
        seasonal_adjustment="SA",
        provider="fred",
    )


def _metric(metric_id: str = "m1") -> SentimentMetric:
    return SentimentMetric(
        metric_id=metric_id,
        entity_id="equity:NASDAQ:AAPL",
        entity_type=NodeType.ASSET,
        score=-0.42,
        confidence=0.87,
        timestamp=_ts(2026, 10, 10, 14, 30),
        source_reference="finnhub:news:abc123",
        model_id="stub@0",
        text_hash="a" * 64,
    )


def _edge(**overrides) -> GraphEdge:
    defaults = {
        "src_node": "FRED:GDP",
        "dst_node": "equity:NASDAQ:AAPL",
        "rel_type": RelationType.INFLUENCES,
        "weight": -0.5,
        "confidence": 0.9,
        "valid_from": _ts(2026, 1, 1),
        "observation_count": 12,
    }
    defaults.update(overrides)
    return GraphEdge(**defaults)


class TestAssetIdempotency:
    """Test asset idempotency."""
    def test_same_id_upserts_once(self, repo):
        """Test that same id upserts once."""
        repo.upsert_asset(_asset())
        repo.upsert_asset(_asset())
        repo.upsert_asset(_asset())
        assert repo.counts()["asset"] == 1

    def test_update_changes_the_row_not_adds(self, repo):
        """Test that update changes the row not adds."""
        repo.upsert_asset(_asset())
        repo.upsert_asset(
            Asset("equity:NASDAQ:AAPL", "AAPL", "Apple Inc",
                  AssetClass.EQUITY, "NASDAQ", "USD")
        )
        assert repo.counts()["asset"] == 1
        rows = repo.db.execute("SELECT name FROM asset").fetchall()
        assert rows[0][0] == "Apple Inc"

    def test_different_ids_are_distinct(self, repo):
        """Test that different ids are distinct."""
        repo.upsert_asset(_asset())
        repo.upsert_asset(
            Asset("equity:NASDAQ:MSFT", "MSFT", "Microsoft",
                  AssetClass.EQUITY, "NASDAQ", "USD")
        )
        assert repo.counts()["asset"] == 2


class TestMacroIdempotency:
    """Test macro idempotency."""
    def test_same_code_upserts_once(self, repo):
        """Test that same code upserts once."""
        repo.upsert_macro_indicator(_macro())
        repo.upsert_macro_indicator(_macro())
        assert repo.counts()["macro_indicator"] == 1


class TestSentimentIdempotency:
    """Test sentiment idempotency."""
    def test_same_metric_id_upserts_once(self, repo):
        """Test that same metric id upserts once."""
        repo.upsert_sentiment_metric(_metric())
        repo.upsert_sentiment_metric(_metric())
        assert repo.counts()["sentiment_metric"] == 1

    def test_different_models_produce_distinct_rows(self, repo):
        """A re-score must not overwrite the previous model's score.

        The model and revision are part of the identity for exactly this reason
        (data-model.md §1.3).
        """
        repo.upsert_sentiment_metric(_metric("m1"))
        repo.upsert_sentiment_metric(_metric("m2"))
        assert repo.counts()["sentiment_metric"] == 2


class TestEdgeIdempotency:
    """Test edge idempotency."""
    def test_same_key_and_window_upserts_once(self, repo):
        """Test that same key and window upserts once."""
        repo.upsert_edge(_edge())
        repo.upsert_edge(_edge())
        repo.upsert_edge(_edge())
        assert repo.counts()["graph_edge"] == 1

    def test_update_changes_the_existing_row(self, repo):
        """Test that update changes the existing row."""
        repo.upsert_edge(_edge(weight=-0.5))
        repo.upsert_edge(_edge(weight=-0.8))
        assert repo.counts()["graph_edge"] == 1
        edge = repo.get_edge(
            "FRED:GDP", "equity:NASDAQ:AAPL", "INFLUENCES", _ts(2026, 1, 1)
        )
        cols = [d[0] for d in repo.db.execute(
            "SELECT * FROM graph_edge LIMIT 0"
        ).description]
        assert dict(zip(cols, edge, strict=True))["weight"] == -0.8

    def test_different_windows_are_distinct_rows(self, repo):
        """Test that different windows are distinct rows."""
        repo.upsert_edge(_edge(valid_from=_ts(2026, 1, 1)))
        repo.upsert_edge(_edge(valid_from=_ts(2026, 4, 1)))
        assert repo.counts()["graph_edge"] == 2

    def test_different_relation_types_are_distinct(self, repo):
        """Test that different relation types are distinct."""
        repo.upsert_edge(_edge(rel_type=RelationType.INFLUENCES))
        repo.upsert_edge(_edge(rel_type=RelationType.CORRELATES_WITH))
        assert repo.counts()["graph_edge"] == 2

    def test_direction_matters(self, repo):
        """Test that direction matters."""
        repo.upsert_edge(_edge())
        repo.upsert_edge(_edge(
            src_node="equity:NASDAQ:AAPL", dst_node="FRED:GDP"
        ))
        assert repo.counts()["graph_edge"] == 2

    def test_bulk_upsert_is_idempotent(self, repo):
        """Test that bulk upsert is idempotent."""
        edges = [_edge(valid_from=_ts(2026, 1, 1)), _edge(valid_from=_ts(2026, 4, 1))]
        repo.upsert_edges(edges)
        repo.upsert_edges(edges)
        assert repo.counts()["graph_edge"] == 2


class TestConstraintEnforcement:
    """T056 — VR-1..VR-4 must hold in the database, not only in the dataclass."""

    @pytest.mark.parametrize(
        "score,confidence",
        [(1.5, 0.5), (-1.5, 0.5), (0.5, 1.5), (0.5, -0.1), (float("nan"), 0.5)],
    )
    def test_out_of_range_metrics_are_rejected(self, repo, score, confidence):
        """Test that out of range metrics are rejected."""
        # ValueError from construction, or duckdb.Error from the DB constraint.
        with pytest.raises((ValueError, duckdb.Error)):
            repo.upsert_sentiment_metric(
                SentimentMetric(
                    metric_id="bad",
                    entity_id="equity:NASDAQ:AAPL",
                    entity_type=NodeType.ASSET,
                    score=score,
                    confidence=confidence,
                    timestamp=_ts(2026, 1, 1),
                    source_reference="r",
                    model_id="m",
                    text_hash="h",
                )
            )

    @pytest.mark.parametrize("weight,confidence", [(2.0, 0.5), (-1.2, 0.5), (0.5, 1.4)])
    def test_out_of_range_edges_are_rejected(self, repo, weight, confidence):
        """Test that out of range edges are rejected."""
        with pytest.raises((ValueError, duckdb.Error)):
            repo.upsert_edge(_edge(weight=weight, confidence=confidence))

    def test_valid_negative_weight_is_accepted(self, repo):
        """Negative weight is semantically meaningful, not an error."""
        repo.upsert_edge(_edge(weight=-0.95))
        assert repo.counts()["graph_edge"] == 1

    def test_unknown_relation_type_is_rejected(self, repo):
        """Test that unknown relation type is rejected."""
        from src.graph.schema import RelationType

        with pytest.raises(ValueError):
            RelationType("CAUSES")

    def test_unknown_asset_class_is_rejected(self, repo):
        """Test that unknown asset class is rejected."""
        from src.graph.schema import AssetClass

        with pytest.raises(ValueError):
            AssetClass("commodity")


class TestNaiveDatestamps:
    """VR-5 cannot be enforced without an unambiguous clock."""

    def test_naive_metric_timestamp_is_rejected(self, repo):
        """Test that naive metric timestamp is rejected."""
        with pytest.raises(ValueError):
            repo.upsert_sentiment_metric(
                SentimentMetric(
                    metric_id="n",
                    entity_id="equity:NASDAQ:AAPL",
                    entity_type=NodeType.ASSET,
                    score=0.1,
                    confidence=0.5,
                    timestamp=datetime(2026, 1, 1),  # naive
                    source_reference="r",
                    model_id="m",
                    text_hash="h",
                )
            )

    def test_naive_edge_window_is_rejected(self, repo):
        """Test that naive edge window is rejected."""
        with pytest.raises(ValueError):
            repo.upsert_edge(_edge(valid_from=datetime(2026, 1, 1)))
