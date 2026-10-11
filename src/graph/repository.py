"""Graph repository: idempotent node and edge upserts (T059, T061).

Idempotency is the requirement that makes re-running a window safe: upserting the
same ``(key, window)`` twice must produce one row, not two. The primary keys
mirror ``data-model.md`` §1–§2 exactly, and the idempotency is a property of the
keys rather than of careful application code.

The repository owns no alignment logic — it writes what it is given, after the
caller has aligned. That separation is what keeps a bug in one from masquerading
as the other.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from src.graph.align import AlignmentError
from src.graph.schema import (
    Asset,
    AssetClass,
    Frequency,
    GraphDatabase,
    GraphEdge,
    MacroIndicator,
    RelationType,
    SentimentMetric,
)

UTC = UTC


class RepositoryError(RuntimeError):
    """Raised when an upsert cannot be performed as specified."""


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AlignmentError(
            "graph timestamps must be timezone-aware: the no-lookahead invariant "
            "(VR-5) cannot be enforced without an unambiguous clock."
        )
    return value.astimezone(UTC)


class GraphRepository:
    """Reads and writes nodes and edges through an embedded DuckDB store."""

    def __init__(self, database: GraphDatabase | None = None) -> None:
        """Initialise the instance."""
        self._db = database

    # ------------------------------------------------------------------
    # Resource management
    # ------------------------------------------------------------------
    @property
    def db(self) -> GraphDatabase:
        """Handle the db."""
        if self._db is None:
            from src.graph.schema import get_database

            self._db = get_database()
        return self._db

    def close(self) -> None:
        """Handle the close."""
        if self._db is not None:
            self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Node upserts (T059)
    # ------------------------------------------------------------------
    def upsert_asset(self, asset: Asset) -> None:
        """Upsert one Asset. Idempotent on ``asset_id``."""
        self.db.execute(
            "INSERT INTO asset (asset_id, ticker, name, asset_class, exchange,"
            " currency, is_active, updated_at) VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT (asset_id) DO UPDATE SET"
            " ticker=excluded.ticker, name=excluded.name,"
            " asset_class=excluded.asset_class, exchange=excluded.exchange,"
            " currency=excluded.currency, is_active=excluded.is_active,"
            " updated_at=excluded.updated_at",
            [
                asset.asset_id,
                asset.ticker,
                asset.name,
                asset.asset_class.value
                if isinstance(asset.asset_class, AssetClass)
                else str(asset.asset_class),
                asset.exchange,
                asset.currency,
                asset.is_active,
                _as_utc(asset.updated_at),
            ],
        )

    def upsert_macro_indicator(self, indicator: MacroIndicator) -> None:
        """Upsert one MacroIndicator. Idempotent on ``indicator_code``."""
        self.db.execute(
            "INSERT INTO macro_indicator (indicator_code, name, unit, frequency,"
            " seasonal_adjustment, provider, updated_at)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT (indicator_code) DO UPDATE SET"
            " name=excluded.name, unit=excluded.unit, frequency=excluded.frequency,"
            " seasonal_adjustment=excluded.seasonal_adjustment,"
            " provider=excluded.provider, updated_at=excluded.updated_at",
            [
                indicator.indicator_code,
                indicator.name,
                indicator.unit,
                indicator.frequency.value
                if isinstance(indicator.frequency, Frequency)
                else str(indicator.frequency),
                indicator.seasonal_adjustment,
                indicator.provider,
                _as_utc(indicator.updated_at),
            ],
        )

    def upsert_sentiment_metric(self, metric: SentimentMetric) -> None:
        """Upsert one SentimentMetric.

        The metric id already contains the model and revision, so re-scoring with
        a different model creates a *distinct* row rather than overwriting
        (data-model.md §1.3), and scoring the same text with the same model is a
        no-op.
        """
        self.db.execute(
            "INSERT INTO sentiment_metric (metric_id, entity_id, entity_type, score,"
            " confidence, timestamp, source_reference, model_id, text_hash, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT (metric_id) DO UPDATE SET"
            " entity_id=excluded.entity_id, entity_type=excluded.entity_type,"
            " score=excluded.score, confidence=excluded.confidence,"
            " timestamp=excluded.timestamp, source_reference=excluded.source_reference,"
            " model_id=excluded.model_id, text_hash=excluded.text_hash,"
            " created_at=excluded.created_at",
            [
                metric.metric_id,
                metric.entity_id,
                metric.entity_type,
                metric.score,
                metric.confidence,
                _as_utc(metric.timestamp),
                metric.source_reference,
                metric.model_id,
                metric.text_hash,
                _as_utc(metric.created_at or metric.timestamp),
            ],
        )

    # ------------------------------------------------------------------
    # Edge upserts (T061)
    # ------------------------------------------------------------------
    def upsert_edge(self, edge: GraphEdge) -> None:
        """Upsert one edge. Idempotent on ``(src, dst, rel_type, valid_from)``."""
        rel = (
            edge.rel_type.value
            if isinstance(edge.rel_type, RelationType)
            else str(edge.rel_type)
        )
        self.db.execute(
            "INSERT INTO graph_edge (src_node, dst_node, rel_type, weight,"
            " confidence, valid_from, valid_to, observation_count, computed_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT (src_node, dst_node, rel_type, valid_from) DO UPDATE SET"
            " weight=excluded.weight, confidence=excluded.confidence,"
            " valid_to=excluded.valid_to, observation_count=excluded.observation_count,"
            " computed_at=excluded.computed_at",
            [
                edge.src_node,
                edge.dst_node,
                rel,
                edge.weight,
                edge.confidence,
                _as_utc(edge.valid_from),
                _as_utc(edge.valid_to) if edge.valid_to else None,
                edge.observation_count,
                _as_utc(edge.computed_at or edge.valid_from),
            ],
        )

    def upsert_edges(self, edges: Iterable[GraphEdge]) -> None:
        """Upsert many edges, preserving deterministic order."""
        for edge in sorted(
            edges, key=lambda e: (e.src_node, e.dst_node, str(e.rel_type), e.valid_from)
        ):
            self.upsert_edge(edge)

    # ------------------------------------------------------------------
    # Bulk helpers
    # ------------------------------------------------------------------
    def upsert_nodes(
        self,
        assets: Sequence[Asset] = (),
        macros: Sequence[MacroIndicator] = (),
        metrics: Sequence[SentimentMetric] = (),
    ) -> dict[str, int]:
        """Upsert several nodes, returning how many of each were written."""
        for asset in assets:
            self.upsert_asset(asset)
        for macro in macros:
            self.upsert_macro_indicator(macro)
        for metric in metrics:
            self.upsert_sentiment_metric(metric)
        return {
            "assets": len(assets),
            "macro_indicators": len(macros),
            "sentiment_metrics": len(metrics),
        }

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def counts(self) -> dict[str, int]:
        """Handle the counts."""
        return self.db.table_counts()

    def get_edge(
        self, src: str, dst: str, rel_type: str, valid_from: datetime
    ) -> dict[str, Any] | None:
        """Return the edge."""
        rows = self.db.execute(
            "SELECT * FROM graph_edge WHERE src_node=? AND dst_node=? AND rel_type=?"
            " AND valid_from=?",
            [src, dst, rel_type, _as_utc(valid_from)],
        ).fetchall()
        if not rows:
            return None
        return rows[0]

    def edges_from(self, node: str, rel_type: str | None = None) -> list[dict[str, Any]]:
        """Handle the edges from."""
        sql = "SELECT * FROM graph_edge WHERE src_node=?"
        params: list[Any] = [node]
        if rel_type:
            sql += " AND rel_type=?"
            params.append(rel_type)
        return self.db.execute(sql, params).fetchall()


__all__ = ["GraphRepository", "RepositoryError"]
