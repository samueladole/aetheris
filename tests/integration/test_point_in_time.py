"""Point-in-time guarantees: a replay is byte-identical and never sees the future.

Raw payload ids embed the wall-clock fetch time, so the replay ingests once and
runs scoring and graph integration twice from the same stored payloads and
manifest into two fresh DuckDB files.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from src.graph.queries import edges_from
from src.graph.repository import GraphRepository
from src.graph.schema import GraphDatabase
from src.orchestration.steps import run_graph_integration, run_ingest, run_sentiment
from tests.integration.fakes import fixture_routes
from tests.integration.test_pipeline_steps import (
    NOW,
    WL,
    _fred_monthly,
    _registry,
    _tiingo,
    _trading_days,
)

# Every table with the primary key its dump is ordered by.
TABLES = {
    "asset": "asset_id",
    "macro_indicator": "indicator_code",
    "sentiment_metric": "metric_id",
    "macro_observation": "indicator_code, observed_at",
    "sentiment_observation": "metric_id",
    "graph_edge": "src_node, dst_node, rel_type, valid_from",
    "scoring_lineage": "metric_id",
}


def _routes(extra_cpi: dict | None = None) -> dict:
    days = _trading_days(date(2026, 1, 2), date(2026, 10, 16))
    cpi = json.loads(_fred_monthly(date(2025, 12, 1), 10))
    if extra_cpi is not None:
        cpi["observations"].append(extra_cpi)
    return fixture_routes(
        {
            "api.tiingo.com/tiingo/daily/AAPL/": _tiingo(days, 100.0, 0.0, 0.001),
            "api.tiingo.com/tiingo/daily/MSFT/": _tiingo(days, 200.0, 0.3, 0.0005),
            "api.stlouisfed.org": b'{"observations": []}',
            "api.stlouisfed.org|series_id=CPIAUCSL": json.dumps(cpi).encode(),
        }
    )


def _dump(db: GraphDatabase) -> dict[str, list[tuple]]:
    return {
        table: db.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
        for table, order in TABLES.items()
    }


def _integrate(tmp_path, name: str, store, manifest, sentiment) -> GraphDatabase:
    db = GraphDatabase(str(tmp_path / name))
    run_graph_integration(store, GraphRepository(db), manifest, sentiment, WL, at=NOW)
    return db


def test_replay_is_byte_identical(tmp_path):
    """Two replays from one stored ingest produce identical rows in every table."""
    registry, _ = _registry(tmp_path, _routes())
    manifest = run_ingest(registry, WL, now=NOW)
    store = registry.payload_store
    sentiment = run_sentiment(store, manifest, WL)
    first = _integrate(tmp_path, "a.duckdb", store, manifest, run_sentiment(store, manifest, WL))
    second = _integrate(tmp_path, "b.duckdb", store, manifest, sentiment)
    try:
        a, b = _dump(first), _dump(second)
        # The comparison must cover the derived edges, not just raw rows.
        kinds = [row[2] for row in a["graph_edge"]]
        assert kinds.count("CORRELATES_WITH") > 0
        assert kinds.count("INFLUENCES") > 0
        assert all(a[table] for table in ("asset", "macro_observation", "scoring_lineage"))
        assert a == b
    finally:
        first.close()
        second.close()


def test_a_release_after_at_is_not_observed(tmp_path):
    """A CPI value first released the day after ``at`` cannot change any edge."""
    future = {
        "realtime_start": (NOW.date() + timedelta(days=1)).isoformat(),
        "realtime_end": "9999-12-31",
        "date": "2026-10-01",
        "value": "99999.000",
    }

    def influences(label: str, extra: dict | None) -> list[dict]:
        registry, _ = _registry(tmp_path / label, _routes(extra))
        manifest = run_ingest(registry, WL, now=NOW)
        store = registry.payload_store
        db = _integrate(
            tmp_path, f"{label}.duckdb", store, manifest, run_sentiment(store, manifest, WL)
        )
        try:
            return edges_from(db, "FRED:CPIAUCSL", rel_type="INFLUENCES")
        finally:
            db.close()

    (tmp_path / "without").mkdir()
    (tmp_path / "with").mkdir()
    without = influences("without", None)
    with_future = influences("with", future)

    assert without, "no INFLUENCES edges, so the comparison would be vacuous"
    assert with_future == without
