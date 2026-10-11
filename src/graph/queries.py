"""1–2 hop graph queries (T062).

The v1 query set is deliberately shallow. Everything the pipeline and its
consumers actually need — sentiment windows, correlation screens, influencer
ranking — is one or two hops, so the queries are plain SQL with explicit indexes
rather than a recursive-traversal engine.

Traversal depth is capped by the read-only API contract (`graph-api.md`). The
underlying schema supports recursive CTEs, but the cap is what keeps the read
surface small (YAGNI); raising it is a spec amendment, not a configuration
change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.graph.schema import GraphDatabase

UTC = UTC


class QueryError(RuntimeError):
    """Raised when a query cannot be answered as specified."""


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise QueryError(
            "query times must be timezone-aware: the no-lookahead invariant "
            "(VR-5) cannot be enforced without an unambiguous clock."
        )
    return value.astimezone(UTC)


def _rows(db: GraphDatabase, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    """Execute and return rows as dicts, so column order is never a contract."""
    result = db.execute(sql, params)
    names = [d[0] for d in result.description]
    return [dict(zip(names, row, strict=True)) for row in result.fetchall()]


def sentiment_series(
    db: GraphDatabase,
    entity_id: str,
    *,
    start: datetime,
    end: datetime,
    min_confidence: float = 0.0,
) -> list[dict[str, Any]]:
    """Sentiment points for one entity in a half-open window, ascending.

    ``min_confidence`` is applied **before** any limit the caller imposes, so a
    page of results never mixes qualifying and non-qualifying rows.
    """
    start, end = _as_utc(start), _as_utc(end)
    if start >= end:
        raise QueryError(
            f"invalid window: start {start.isoformat()} is not before end "
            f"{end.isoformat()}"
        )
    if not 0.0 <= min_confidence <= 1.0:
        raise QueryError(f"min_confidence {min_confidence} outside [0.0, 1.0]")

    return _rows(
        db,
        "SELECT metric_id, entity_id, score, confidence, timestamp,"
        " source_reference, model_id FROM sentiment_metric"
        " WHERE entity_id = ? AND timestamp >= ? AND timestamp < ?"
        " AND confidence >= ?"
        " ORDER BY timestamp ASC",
        [entity_id, start, end, min_confidence],
    )


def edges_from(
    db: GraphDatabase,
    node: str,
    *,
    rel_type: str | None = None,
    min_confidence: float = 0.0,
) -> list[dict[str, Any]]:
    """Outbound edges for one node, optionally filtered by relation type."""
    sql = (
        "SELECT src_node, dst_node, rel_type, weight, confidence,"
        " observation_count, valid_from, valid_to FROM graph_edge WHERE src_node = ?"
    )
    params: list[Any] = [node]
    if rel_type:
        sql += " AND rel_type = ?"
        params.append(rel_type)
    sql += " AND confidence >= ? ORDER BY confidence DESC, ABS(weight) DESC"
    params.append(min_confidence)
    return _rows(db, sql, params)


def edges_to(
    db: GraphDatabase,
    node: str,
    *,
    rel_type: str | None = None,
    min_confidence: float = 0.0,
) -> list[dict[str, Any]]:
    """Inbound edges for one node, optionally filtered by relation type."""
    sql = (
        "SELECT src_node, dst_node, rel_type, weight, confidence,"
        " observation_count, valid_from, valid_to FROM graph_edge WHERE dst_node = ?"
    )
    params: list[Any] = [node]
    if rel_type:
        sql += " AND rel_type = ?"
        params.append(rel_type)
    sql += " AND confidence >= ? ORDER BY confidence DESC, ABS(weight) DESC"
    params.append(min_confidence)
    return _rows(db, sql, params)


def top_influencers(
    db: GraphDatabase,
    node: str,
    *,
    limit: int = 10,
    min_confidence: float = 0.5,
) -> list[dict[str, Any]]:
    """Ranked ``INFLUENCES`` edges into ``node``.

    Ranked by confidence then absolute weight: a strong negative influence
    matters as much as a strong positive one, so ordering by signed weight would
    bury it.
    """
    if limit < 1:
        raise QueryError(f"limit must be >= 1; got {limit}")
    sql = (
        "SELECT src_node, dst_node, rel_type, weight, confidence,"
        " observation_count, valid_from, valid_to FROM graph_edge"
        " WHERE dst_node = ? AND rel_type = 'INFLUENCES' AND confidence >= ?"
        " ORDER BY confidence DESC, ABS(weight) DESC LIMIT ?"
    )
    return _rows(db, sql, [node, min_confidence, limit])


def correlated_with(
    db: GraphDatabase,
    node: str,
    *,
    min_abs_weight: float = 0.0,
    min_confidence: float = 0.0,
) -> list[dict[str, Any]]:
    """``CORRELATES_WITH`` edges for one node.

    ``min_abs_weight`` filters on magnitude, so a threshold of 0.7 means
    "strongly correlated in either direction" rather than only positive.
    """
    sql = (
        "SELECT src_node, dst_node, rel_type, weight, confidence,"
        " observation_count, valid_from, valid_to FROM graph_edge"
        " WHERE (src_node = ? OR dst_node = ?) AND rel_type = 'CORRELATES_WITH'"
        " AND confidence >= ? AND ABS(weight) >= ?"
        " ORDER BY ABS(weight) DESC"
    )
    return _rows(db, sql, [node, node, min_confidence, min_abs_weight])


def get_entity(db: GraphDatabase, node_id: str) -> dict[str, Any] | None:
    """Resolve a node id to its type and attributes, or ``None``."""
    for table, key, label in (
        ("asset", "asset_id", "Asset"),
        ("macro_indicator", "indicator_code", "MacroIndicator"),
        ("sentiment_metric", "metric_id", "SentimentMetric"),
    ):
        rows = _rows(
            db,
            f"SELECT * FROM {table} WHERE {key} = ?",
            [node_id],
        )
        if rows:
            return {"node_type": label, "attributes": rows[0]}
    return None


def list_assets(
    db: GraphDatabase,
    *,
    asset_class: str | None = None,
    is_active: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Assets, optionally filtered by class, with offset pagination."""
    sql = "SELECT * FROM asset WHERE is_active = ?"
    params: list[Any] = [is_active]
    if asset_class:
        sql += " AND asset_class = ?"
        params.append(asset_class)
    sql += " ORDER BY asset_id LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return _rows(db, sql, params)


def two_hop_path(
    db: GraphDatabase,
    from_node: str,
    to_node: str,
) -> list[dict[str, Any]] | None:
    """A path of at most two edges from ``from_node`` to ``to_node``.

    Returns the first path found, or ``None``. Depth is capped at two by the
    read-only API contract; anything deeper must be rejected by the caller
    rather than silently truncated here.
    """
    direct = _rows(
        db,
        "SELECT src_node, dst_node, rel_type, weight, confidence FROM graph_edge"
        " WHERE src_node = ? AND dst_node = ? LIMIT 1",
        [from_node, to_node],
    )
    if direct:
        return [direct[0]]

    two_hop = _rows(
        db,
        "SELECT a.src_node, a.dst_node, a.rel_type, a.weight, a.confidence,"
        " b.src_node AS mid_src, b.dst_node AS mid_dst, b.rel_type AS b_rel,"
        " b.weight AS b_weight, b.confidence AS b_conf"
        " FROM graph_edge a JOIN graph_edge b ON a.dst_node = b.src_node"
        " WHERE a.src_node = ? AND b.dst_node = ? LIMIT 1",
        [from_node, to_node],
    )
    if not two_hop:
        return None

    row = two_hop[0]
    return [
        {
            "src_node": row["src_node"],
            "dst_node": row["dst_node"],
            "rel_type": row["rel_type"],
            "weight": row["weight"],
            "confidence": row["confidence"],
        },
        {
            "src_node": row["mid_src"],
            "dst_node": row["mid_dst"],
            "rel_type": row["b_rel"],
            "weight": row["b_weight"],
            "confidence": row["b_conf"],
        },
    ]


__all__ = [
    "QueryError",
    "correlated_with",
    "edges_from",
    "edges_to",
    "get_entity",
    "list_assets",
    "sentiment_series",
    "top_influencers",
    "two_hop_path",
]
