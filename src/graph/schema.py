"""DuckDB schema for the Aetheris knowledge graph (FR-011, FR-012).

The schema is relational-by-design but graph-shaped: typed node tables and
typed relation tables, exactly as specified in data-model.md §1–§2.

The CHECK constraints are **not** redundant with application-level validation.
They enforce VR-1..VR-4 at the storage layer, so a bug anywhere in the writer
path cannot silently persist an out-of-range weight or confidence.
"""

from __future__ import annotations

import threading
from enum import StrEnum

import duckdb


class AssetClass(StrEnum):
    EQUITY = "equity"
    FOREX = "forex"
    CRYPTO = "crypto"
    INDEX = "index"


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class RelationType(StrEnum):
    INFLUENCES = "INFLUENCES"
    CORRELATES_WITH = "CORRELATES_WITH"
    TARGETS = "TARGETS"


class NodeType(StrEnum):
    ASSET = "Asset"
    MACRO_INDICATOR = "MacroIndicator"
    SENTIMENT_METRIC = "SentimentMetric"


SCHEMA_VERSION = "1.0"

_DDL = """
-- ---------------------------------------------------------------------------
-- Node tables (data-model.md §1)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset (
    asset_id     VARCHAR PRIMARY KEY,          -- asset_class:exchange:symbol
    ticker       VARCHAR NOT NULL,
    name         VARCHAR NOT NULL,
    asset_class  VARCHAR NOT NULL CHECK (asset_class IN
                    ('equity', 'forex', 'crypto', 'index')),
    exchange     VARCHAR NOT NULL,
    currency     VARCHAR NOT NULL,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at   TIMESTAMP NOT NULL
);

-- indicator_code is provider-prefixed, so two macro sources can coexist.
CREATE TABLE IF NOT EXISTS macro_indicator (
    indicator_code        VARCHAR PRIMARY KEY,
    name                  VARCHAR NOT NULL,
    unit                  VARCHAR NOT NULL,
    frequency             VARCHAR NOT NULL CHECK (frequency IN
                               ('daily', 'weekly', 'monthly', 'quarterly', 'annual')),
    seasonal_adjustment   VARCHAR NOT NULL,
    provider              VARCHAR NOT NULL,
    updated_at            TIMESTAMP NOT NULL
);

-- Highest-volume node type. Append-only: re-scoring with a new model creates a
-- new row rather than overwriting (data-model.md §1.3).
CREATE TABLE IF NOT EXISTS sentiment_metric (
    metric_id        VARCHAR PRIMARY KEY,      -- entity_id:source_reference:text_hash
    entity_id        VARCHAR NOT NULL,
    entity_type      VARCHAR NOT NULL CHECK (entity_type IN
                          ('Asset', 'MacroIndicator')),
    -- VR-1: score is signed and bounded
    score            DOUBLE NOT NULL CHECK (score BETWEEN -1.0 AND 1.0),
    -- VR-2: confidence is calibrated and bounded
    confidence       DOUBLE NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    timestamp        TIMESTAMP NOT NULL,        -- observation time of the text
    source_reference VARCHAR NOT NULL,
    model_id         VARCHAR NOT NULL,          -- pinned model + revision
    text_hash        VARCHAR NOT NULL,
    created_at       TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sentiment_entity_time
    ON sentiment_metric (entity_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_sentiment_source
    ON sentiment_metric (source_reference);

-- ---------------------------------------------------------------------------
-- Observations backing the graph. Two distinct timestamps per value, because
-- conflating them is how a pipeline leaks future information (VR-5).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS macro_observation (
    indicator_code  VARCHAR NOT NULL,
    -- the period the value describes
    observed_at     TIMESTAMP NOT NULL,
    -- when the value became publicly knowable — the no-lookahead anchor
    available_at    TIMESTAMP NOT NULL,
    value           DOUBLE NOT NULL,
    PRIMARY KEY (indicator_code, observed_at)
);

CREATE TABLE IF NOT EXISTS sentiment_observation (
    metric_id       VARCHAR PRIMARY KEY,
    observed_at     TIMESTAMP NOT NULL,
    available_at    TIMESTAMP NOT NULL,
    FOREIGN KEY (metric_id) REFERENCES sentiment_metric(metric_id)
);

-- ---------------------------------------------------------------------------
-- Edge tables (data-model.md §2)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS graph_edge (
    src_node          VARCHAR NOT NULL,
    dst_node          VARCHAR NOT NULL,
    rel_type          VARCHAR NOT NULL CHECK (rel_type IN
                          ('INFLUENCES', 'CORRELATES_WITH', 'TARGETS')),
    -- VR-3: weight is signed and bounded
    weight            DOUBLE NOT NULL CHECK (weight BETWEEN -1.0 AND 1.0),
    -- VR-4: confidence is bounded
    confidence        DOUBLE NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    valid_from        TIMESTAMP NOT NULL,
    valid_to          TIMESTAMP,               -- NULL = currently open
    observation_count INTEGER NOT NULL DEFAULT 0,
    computed_at       TIMESTAMP NOT NULL,
    -- VR-6: idempotent upsert key — identical (key, window) is a no-op
    PRIMARY KEY (src_node, dst_node, rel_type, valid_from)
);

CREATE INDEX IF NOT EXISTS idx_edge_src ON graph_edge (src_node, rel_type);
CREATE INDEX IF NOT EXISTS idx_edge_dst ON graph_edge (dst_node, rel_type);
CREATE INDEX IF NOT EXISTS idx_edge_window ON graph_edge (valid_from, valid_to);

-- ---------------------------------------------------------------------------
-- Provenance: every sentiment row traces to a raw payload (VR-7)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scoring_lineage (
    metric_id  VARCHAR PRIMARY KEY,
    payload_id VARCHAR NOT NULL,
    text_hash  VARCHAR NOT NULL,
    model_id   VARCHAR NOT NULL
);
"""


class GraphSchemaError(RuntimeError):
    """Raised when the graph schema cannot be created or verified."""


class GraphDatabase:
    """Owns the DuckDB connection and guarantees the schema exists."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._conn = duckdb.connect(db_path)
        self._lock = threading.Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        try:
            with self._lock:
                self._conn.execute(_DDL)
        except Exception as exc:  # pragma: no cover - corrupt/incompatible file
            raise GraphSchemaError(f"Failed to create graph schema: {exc}") from exc

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        return self._conn

    # ------------------------------------------------------------------
    def verify(self) -> bool:
        """Cheap structural self-check, used by `aetheris status` and CI."""
        expected = {
            "asset",
            "macro_indicator",
            "sentiment_metric",
            "macro_observation",
            "sentiment_observation",
            "graph_edge",
            "scoring_lineage",
        }
        with self._lock:
            rows = self._conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        found = {r[0] for r in rows}
        return expected.issubset(found)

    def table_counts(self) -> dict[str, int]:
        with self._lock:
            tables = [
                "asset",
                "macro_indicator",
                "sentiment_metric",
                "graph_edge",
                "macro_observation",
            ]
            return {
                t: int(self._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
                for t in tables
            }

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    def execute(self, sql: str, params: list[object] | None = None):
        with self._lock:
            return self._conn.execute(sql, params or [])


_db_singleton: GraphDatabase | None = None
_singleton_lock = threading.Lock()


def get_database(db_path: str = ":memory:") -> GraphDatabase:
    """Process-wide default graph database."""
    global _db_singleton
    if _db_singleton is None:
        with _singleton_lock:
            if _db_singleton is None:
                _db_singleton = GraphDatabase(db_path)
    return _db_singleton


def reset_database() -> None:
    """Tear down the singleton (test isolation)."""
    global _db_singleton
    if _db_singleton is not None:
        _db_singleton.close()
        _db_singleton = None
