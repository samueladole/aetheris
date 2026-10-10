"""Unit tests for the DuckDB graph schema (T056).

Verifies that the CHECK constraints actually bite at the storage layer — not
merely that the tables exist. A bug in any writer path must not be able to
persist an out-of-range weight or confidence (VR-1 .. VR-4).
"""

from __future__ import annotations

import duckdb
import pytest

from src.graph.schema import (
    AssetClass,
    Frequency,
    GraphDatabase,
    NodeType,
    RelationType,
    reset_database,
)


@pytest.fixture()
def db():
    """Test that db."""
    database = GraphDatabase(":memory:")
    yield database
    database.close()
    reset_database()


def _now():
    import datetime

    return datetime.datetime.now(datetime.UTC)


def _insert(database: GraphDatabase, sql: str):
    return database.execute(sql)


class TestSchemaStructure:
    """Test schema structure."""
    def test_all_tables_exist(self, db):
        """Test that all tables exist."""
        assert db.verify()

    def test_row_counts_start_at_zero(self, db):
        """Test that row counts start at zero."""
        counts = db.table_counts()
        assert set(counts) >= {"asset", "macro_indicator", "sentiment_metric", "graph_edge"}
        assert all(v == 0 for v in counts.values())

    def test_enum_values(self):
        """Test that enum values."""
        assert AssetClass.EQUITY.value == "equity"
        assert Frequency.MONTHLY.value == "monthly"
        assert RelationType.TARGETS.value == "TARGETS"
        assert NodeType.SENTIMENT_METRIC.value == "SentimentMetric"


class TestValidationRuleConstraints:
    """VR-1 .. VR-4 must be enforced by the database, not only by app code."""

    def test_vr1_score_above_one_rejected(self, db):
        """Test that vr1 score above one rejected."""
        with pytest.raises(duckdb.Error):
            _insert(
                db,
                "INSERT INTO sentiment_metric VALUES ('m','a:1','Asset',1.5,0.5,"
                "now(),'ref','model','h',now())",
            )

    def test_vr1_score_below_minus_one_rejected(self, db):
        """Test that vr1 score below minus one rejected."""
        with pytest.raises(duckdb.Error):
            _insert(
                db,
                "INSERT INTO sentiment_metric VALUES ('m','a:1','Asset',-1.5,0.5,"
                "now(),'ref','model','h',now())",
            )

    def test_vr2_confidence_above_one_rejected(self, db):
        """Test that vr2 confidence above one rejected."""
        with pytest.raises(duckdb.Error):
            _insert(
                db,
                "INSERT INTO sentiment_metric VALUES ('m','a:1','Asset',0.5,1.5,"
                "now(),'ref','model','h',now())",
            )

    def test_vr3_weight_above_one_rejected(self, db):
        """Test that vr3 weight above one rejected."""
        with pytest.raises(duckdb.Error):
            _insert(
                db,
                "INSERT INTO graph_edge VALUES ('a','b','INFLUENCES',1.5,0.5,"
                "now(),NULL,1,now())",
            )

    def test_vr4_confidence_below_zero_rejected(self, db):
        """Test that vr4 confidence below zero rejected."""
        with pytest.raises(duckdb.Error):
            _insert(
                db,
                "INSERT INTO graph_edge VALUES ('a','b','TARGETS',0.5,-0.1,"
                "now(),NULL,1,now())",
            )

    def test_valid_sentiment_is_accepted(self, db):
        """Test that valid sentiment is accepted."""
        _insert(
            db,
            "INSERT INTO sentiment_metric VALUES ('m1','equity:NASDAQ:AAPL','Asset',"
            "-0.42,0.87,now(),'finnhub:news:1','m:rev','h',now())",
        )
        row = db.execute("SELECT score, confidence FROM sentiment_metric").fetchone()
        assert row[0] == -0.42
        assert row[1] == 0.87

    def test_signed_weight_is_preserved(self, db):
        """Negative weight must be accepted — it is semantically meaningful."""
        _insert(
            db,
            "INSERT INTO graph_edge VALUES ('FRED:GDP','equity:NASDAQ:AAPL',"
            "'INFLUENCES',-0.6,0.9,now(),NULL,12,now())"
        )
        row = db.execute("SELECT weight FROM graph_edge").fetchone()
        assert row[0] == -0.6

    def test_unknown_asset_class_rejected(self, db):
        """Test that unknown asset class rejected."""
        with pytest.raises(duckdb.Error):
            _insert(
                db,
                "INSERT INTO asset VALUES ('x','x','X','commodity','NASDAQ','USD',"
                "TRUE,now())",
            )

    def test_unknown_relation_type_rejected(self, db):
        """Test that unknown relation type rejected."""
        with pytest.raises(duckdb.Error):
            _insert(
                db,
                "INSERT INTO graph_edge VALUES ('a','b','CAUSES',0.5,0.5,"
                "now(),NULL,1,now())",
            )
