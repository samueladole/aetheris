"""Read-only, in-process graph API (T063).

The public surface for the Knowledge Graph. It is deliberately **read-only and
in-process**: no network service, no auth surface, no mutation path. A clarifying
decision fixed the scope as documenting the existing CLI and Python library, so
anything that looks like a server here would be scope creep.

Every function is a projection over `src/graph/queries.py` — the API adds
contract enforcement and typed returns, never new query logic. That keeps the
read surface in one auditable place.

Traversal is capped at two hops. The underlying schema supports recursive CTEs,
but the cap keeps the surface small; raising it is a spec amendment, not a
configuration change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from src.graph import queries
from src.graph.schema import GraphDatabase, get_database

UTC = UTC
MAX_HOPS = 2


class GraphAPIError(RuntimeError):
    """Base class for API errors."""


class UnknownEntityError(GraphAPIError):
    """A referenced node id does not resolve to any entity."""


class InvalidWindowError(GraphAPIError):
    """A time window is malformed, e.g. timezone-naive or inverted."""


class UnsupportedTraversalError(GraphAPIError):
    """A traversal deeper than the v1 contract allows."""


@dataclass(frozen=True)
class Entity:
    """A resolved node with its attributes."""

    node_id: str
    node_type: str
    attributes: dict[str, object]


def _require_aware(value: datetime, field: str) -> datetime:
    # VR-5: an ambiguous clock makes point-in-time correctness unenforceable.
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidWindowError(
            f"{field} is timezone-naive. Pass an aware UTC datetime."
        )
    return value.astimezone(UTC)


def _db(db: GraphDatabase | None) -> GraphDatabase:
    return db if db is not None else get_database()


def get_entity(node_id: str, *, db: GraphDatabase | None = None) -> Entity | None:
    """Resolve a node id, or return ``None`` if it is absent."""
    found = queries.get_entity(_db(db), node_id)
    if found is None:
        return None
    return Entity(
        node_id=node_id,
        node_type=str(found["node_type"]),
        attributes=found["attributes"],  # type: ignore[arg-type]
    )


def require_entity(node_id: str, *, db: GraphDatabase | None = None) -> Entity:
    """Resolve a node id or raise :class:`UnknownEntityError`."""
    entity = get_entity(node_id, db=db)
    if entity is None:
        raise UnknownEntityError(f"no node with id {node_id!r}")
    return entity


def sentiment_for(
    entity_id: str,
    *,
    start: datetime,
    end: datetime,
    min_confidence: float = 0.0,
    limit: int | None = None,
    db: GraphDatabase | None = None,
) -> list[dict[str, object]]:
    """Sentiment time series for one entity over ``[start, end)``.

    ``min_confidence`` is applied before ``limit``, so a page never mixes
    qualifying and non-qualifying rows.
    """
    _require_aware(start, "start")
    _require_aware(end, "end")
    if start >= end:
        # Validate here rather than delegating: the query layer raises its own
        # error type, and callers of this API contract expect InvalidWindowError.
        raise InvalidWindowError(
            f"invalid window: start {start.isoformat()} is not before end "
            f"{end.isoformat()}"
        )
    if not 0.0 <= min_confidence <= 1.0:
        raise InvalidWindowError(
            f"min_confidence {min_confidence} outside [0.0, 1.0]"
        )
    if limit is not None and limit < 1:
        raise InvalidWindowError(f"limit must be >= 1; got {limit}")

    rows = queries.sentiment_series(
        _db(db), entity_id, start=start, end=end, min_confidence=min_confidence
    )
    return rows[:limit] if limit else rows


def edges_from(
    node_id: str,
    *,
    rel_type: str | None = None,
    min_weight: float | None = None,
    min_confidence: float = 0.0,
    limit: int | None = None,
    db: GraphDatabase | None = None,
) -> list[dict[str, object]]:
    """Outbound edges for one node.

    ``min_weight`` compares on the **absolute** value, so a threshold of 0.7
    means "strongly correlated in either direction" rather than only positive.
    """
    rows = queries.edges_from(
        _db(db), node_id, rel_type=rel_type, min_confidence=min_confidence
    )
    if min_weight is not None:
        rows = [r for r in rows if abs(float(r["weight"])) >= min_weight]  # type: ignore[arg-type]
    return rows[:limit] if limit else rows


def edges_to(
    node_id: str,
    *,
    rel_type: str | None = None,
    min_confidence: float = 0.0,
    limit: int | None = None,
    db: GraphDatabase | None = None,
) -> list[dict[str, object]]:
    """Inbound edges for one node."""
    rows = queries.edges_to(
        _db(db), node_id, rel_type=rel_type, min_confidence=min_confidence
    )
    return rows[:limit] if limit else rows


def top_influencers(
    node_id: str,
    *,
    limit: int = 10,
    min_confidence: float = 0.5,
    db: GraphDatabase | None = None,
) -> list[dict[str, object]]:
    """Ranked ``INFLUENCES`` edges into ``node_id``, by confidence then magnitude."""
    return queries.top_influencers(
        _db(db), node_id, limit=limit, min_confidence=min_confidence
    )


def correlated_with(
    node_id: str,
    *,
    min_abs_weight: float = 0.0,
    min_confidence: float = 0.0,
    db: GraphDatabase | None = None,
) -> list[dict[str, object]]:
    """``CORRELATES_WITH`` edges for one node, ordered by magnitude."""
    return queries.correlated_with(
        _db(db),
        node_id,
        min_abs_weight=min_abs_weight,
        min_confidence=min_confidence,
    )


def path(
    from_node: str,
    to_node: str,
    *,
    max_hops: int = 2,
    db: GraphDatabase | None = None,
) -> list[dict[str, object]] | None:
    """A path of at most ``max_hops`` edges, or ``None`` if none exists.

    Deeper than :data:`MAX_HOPS` is refused rather than truncated — silently
    returning a shorter path would answer a different question than the one asked.
    """
    if max_hops > MAX_HOPS:
        raise UnsupportedTraversalError(
            f"max_hops {max_hops} exceeds the v1 contract limit of {MAX_HOPS}. "
            "The schema supports deeper traversal; raising the cap is a spec "
            "amendment, not a configuration change."
        )
    if max_hops < 1:
        raise InvalidWindowError(f"max_hops must be >= 1; got {max_hops}")
    return queries.two_hop_path(_db(db), from_node, to_node)


def list_assets(
    *,
    asset_class: str | None = None,
    is_active: bool = True,
    limit: int = 100,
    offset: int = 0,
    db: GraphDatabase | None = None,
) -> list[dict[str, object]]:
    """Assets with offset pagination — acceptable at v1 scale."""
    if limit < 1:
        raise InvalidWindowError(f"limit must be >= 1; got {limit}")
    if offset < 0:
        raise InvalidWindowError(f"offset must be >= 0; got {offset}")
    return queries.list_assets(
        _db(db),
        asset_class=asset_class,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )


def known_node_ids(
    *, asset_class: str | None = None, db: GraphDatabase | None = None
) -> list[str]:
    """Every asset id, optionally filtered. Used for validation and status."""
    return [
        str(row["asset_id"])  # type: ignore[index]
        for row in list_assets(asset_class=asset_class, db=db)
    ]


__all__ = [
    "MAX_HOPS",
    "Entity",
    "GraphAPIError",
    "InvalidWindowError",
    "UnknownEntityError",
    "UnsupportedTraversalError",
    "correlated_with",
    "edges_from",
    "edges_to",
    "get_entity",
    "known_node_ids",
    "list_assets",
    "path",
    "require_entity",
    "sentiment_for",
    "top_influencers",
]
