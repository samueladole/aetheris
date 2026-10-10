# Contract: Read-Only Graph Query API (FR-021)

**Decision**: read-only, **in-process Python API**. No network service, no HTTP, no auth surface.

**Module**: `src/graph/api.py`
**Status**: all functions are pure reads; nothing in this module may mutate graph state.

---

## Types

```python
Score = float          # [-1.0, 1.0], signed
Confidence = float     # [0.0, 1.0], calibrated
NodeRef = str          # an Asset.asset_id or MacroIndicator.indicator_code
Relation = Literal["INFLUENCES", "CORRELATES_WITH", "TARGETS"]
```

```python
@dataclass(frozen=True)
class SentimentPoint:
    metric_id: str
    entity_id: str
    score: Score
    confidence: Confidence
    timestamp: datetime     # UTC, observation time of the text
    source_reference: str

@dataclass(frozen=True)
class Edge:
    src_node: NodeRef
    dst_node: NodeRef
    rel_type: Relation
    weight: Score
    confidence: Confidence
    observation_count: int
    valid_from: datetime
    valid_to: datetime | None

@dataclass(frozen=True)
class Entity:
    node_id: NodeRef
    node_type: Literal["Asset", "MacroIndicator", "SentimentMetric"]
    attributes: Mapping[str, object]   # type-dependent; see data-model.md §1
```

## Surface

| Function | Returns | Semantics |
|---|---|---|
| `get_entity(node_id) -> Entity \| None` | Single entity or `None` | Type dispatched from `node_id` |
| `sentiment_for(entity_id, start, end, *, min_confidence=0.0) -> list[SentimentPoint]` | Time series | `start`/`end` UTC, half-open `[start, end)`; sorted ascending by `timestamp`; `min_confidence` applied **before** pagination |
| `edges_from(node_id, rel_type=None, *, min_weight=None, min_confidence=None, window=None) -> list[Edge]` | 1-hop | `min_weight` compares on the **absolute** value so `\|r\| ≥ θ` means "strongly correlated, either sign" |
| `edges_to(node_id, rel_type=None, *, min_confidence=None) -> list[Edge]` | 1-hop | Inbound edges |
| `top_influencers(node_id, limit=10, *, min_confidence=0.5) -> list[Edge]` | Ranked | `INFLUENCES` into `node_id`, ordered by `confidence DESC, abs(weight) DESC` |
| `path(from_node, to_node, max_hops=2) -> list[list[Edge]] \| None` | ≤2-hop path | Raises `UnsupportedTraversal` if `max_hops > 2` in v1 (see Limits) |
| `list_assets(asset_class=None, *, is_active=True, limit=100, offset=0) -> list[Entity]` | Paginated | Cursor-free offset pagination is acceptable at v1 scale |

## Errors

```python
class GraphAPIError(Exception): ...
class UnknownEntityError(GraphAPIError): ...      # node_id referenced but absent
class InvalidWindowError(GraphAPIError): ...      # start >= end, or tz-naive datetime
class UnsupportedTraversalError(GraphAPIError): ...  # max_hops > 2 in v1
```

All datetimes are **timezone-aware UTC**. Passing a naive datetime raises `InvalidWindowError` —
this exists because the no-lookahead invariant (VR-5) is unenforceable without an unambiguous clock.

## Guarantees

- **Point-in-time**: `sentiment_for` and `edges_from` never expose a value whose availability
  timestamp is later than the requested window end. Callers querying "the world as of T" get exactly
  the world as of T.
- **Determinism**: identical inputs yield identical outputs; no hidden state, no caching layer that
  changes results.
- **Read-only**: no function in this module opens a write transaction.

## Limits (v1 — deliberate scope)

- Max traversal depth 2 hops. The underlying DuckDB schema supports recursive CTEs, but the contract
  is capped so the read surface stays small (YAGNI). Raise the cap behind a spec amendment when a real
  consumer needs deeper traversal, and revisit the LadybugDB question from `research.md` §B at the
  same time.
- No aggregation/analytics endpoint (moving averages, cross-asset screening). Belongs to the CLI or a
  downstream consumer, not the storage layer.
