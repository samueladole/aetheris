"""Pipeline step functions: ingest, score, integrate into the graph, evaluate.

Every provider response comes from ``RoutedHttpClient``, serving the Task 3
fixtures or synthetic series built in this file, so no test makes a live
provider call. Each test writes to its own raw store and DuckDB file.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from src.config.providers import get_registry
from src.config.settings import Settings
from src.config.watchlist import DEFAULT_WATCHLIST, WatchedAsset, Watchlist
from src.graph.queries import edges_from, sentiment_scores
from src.graph.repository import GraphRepository
from src.graph.schema import GraphDatabase, NodeType, SentimentMetric
from src.ingestion.base import ProviderUnavailable
from src.ingestion.parsers import AssetProfile, parse_fred_observations
from src.ingestion.registry import RegistryError
from src.orchestration.steps import (
    IngestError,
    build_watch_terms,
    manifest_from_store,
    run_evaluation,
    run_graph_integration,
    run_ingest,
    run_sentiment,
)
from src.orchestration.wiring import build_ingestion_registry
from src.sentiment.entities import WatchTerms, resolve_entities
from tests.integration.fakes import RoutedHttpClient, UnroutedRequest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers"
NOW = datetime(2026, 10, 20, tzinfo=UTC)
WL = DEFAULT_WATCHLIST
AAPL = "equity:NASDAQ:AAPL"
MSFT = "equity:NASDAQ:MSFT"
PROFILE = "finnhub.io/api/v1/stock/profile2"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _routes(overrides: dict[str, bytes | Exception] | None = None) -> dict:
    """Fixture routes: CPI for every series, AAPL prices and profile, general news."""
    routes: dict[str, bytes | Exception] = {
        "api.stlouisfed.org": _fixture("fred_cpiaucsl.json"),
        "api.tiingo.com/tiingo/daily": b"[]",
        "api.tiingo.com/tiingo/daily/AAPL/": _fixture("tiingo_aapl_prices.json"),
        PROFILE: b"{}",
        f"{PROFILE}|symbol=AAPL": _fixture("finnhub_profile_aapl.json"),
        "finnhub.io|category=general": _fixture("finnhub_news_general.json"),
    }
    routes.update(overrides or {})
    return routes


class _Unlimited:
    """A rate limiter that never waits.

    Tiingo's bucket allows one request a minute, so eight tickers would stall
    the test for seven minutes behind the real limiter.
    """

    def acquire(self, spec, *, timeout_s: float = 30.0) -> None:
        """Grant every request immediately."""

    def snapshot(self) -> dict[str, float]:
        """Report no buckets."""
        return {}


def _settings(tmp_path) -> Settings:
    return Settings(
        raw_db_path=str(tmp_path / "raw.db"),
        fred_api_key="fred-key",
        finnhub_api_key="finnhub-key",
        tiingo_api_key="tiingo-key",
        activity_timeout_s=1.0,
    )


def _registry(tmp_path, routes: dict | None = None):
    http = RoutedHttpClient(routes if routes is not None else _routes())
    registry = build_ingestion_registry(_settings(tmp_path), http=http)
    registry.rate_limiter = _Unlimited()
    return registry, http


def _ingest(tmp_path, routes: dict | None = None, watchlist: Watchlist = WL):
    registry, _ = _registry(tmp_path, routes)
    manifest = run_ingest(registry, watchlist, now=NOW)
    return registry.payload_store, manifest


@pytest.fixture()
def db(tmp_path):
    """A fresh DuckDB graph file, closed after the test."""
    database = GraphDatabase(str(tmp_path / "g.duckdb"))
    yield database
    database.close()


def _keys(manifest: dict) -> set[tuple[str, str]]:
    return {(e["kind"], e["key"]) for e in manifest["entries"]}


def _integrate(tmp_path, db, routes: dict | None = None):
    store, manifest = _ingest(tmp_path, routes)
    sentiment = run_sentiment(store, manifest, WL)
    repo = GraphRepository(db)
    out = run_graph_integration(store, repo, manifest, sentiment, WL, at=NOW)
    return store, manifest, sentiment, repo, out


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_build_ingestion_registry_registers_every_provider(tmp_path):
    """Every declared endpoint has an adapter, and credentials come from settings."""
    registry, _ = _registry(tmp_path)
    for spec in get_registry().all():
        adapters = registry.adapters_for(spec.name)
        for endpoint in spec.endpoints:
            assert any(a.can_handle(endpoint) for a in adapters), (spec.name, endpoint)

    fred = registry.adapters_for("fred")[0]
    request = fred.build_request("series/observations", {"series_id": "GDP"})
    assert request.params["api_key"] == "fred-key"


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def test_ingest_manifest_covers_the_watchlist(tmp_path):
    """One entry per series, ticker price and profile, plus the news feed."""
    registry, http = _registry(tmp_path)
    m = run_ingest(registry, WL, now=NOW)

    assert _keys(m) >= {
        ("macro", "CPIAUCSL"),
        ("prices", "AAPL"),
        ("profile", "AAPL"),
        ("news", "general"),
    }
    assert len(m["entries"]) == len(WL.macro) + 2 * len(WL.assets) + 1
    assert m["failures"] == []
    assert m["at"] == NOW.isoformat()
    json.dumps(m)  # DBOS-serialisable

    start = (NOW - timedelta(days=WL.price_history_days)).date().isoformat()
    assert any("AAPL/prices" in c and f"startDate={start}" in c for c in http.calls)
    stored = registry.payload_store.get(
        next(e["payload_id"] for e in m["entries"] if e["kind"] == "prices")
    )
    assert stored is not None and stored.request_params["startDate"] == start


def test_one_failed_series_is_recorded_and_the_rest_continue(tmp_path):
    """A provider failure on one series is a recorded failure, not a crash."""
    down = ProviderUnavailable("fred", "series/observations", 503, "server error")
    registry, _ = _registry(tmp_path, _routes({"api.stlouisfed.org|series_id=UNRATE": down}))

    m = run_ingest(registry, WL, now=NOW)

    assert [(f["kind"], f["key"]) for f in m["failures"]] == [("macro", "UNRATE")]
    assert "server error" in m["failures"][0]["error"]
    assert ("macro", "UNRATE") not in _keys(m)
    assert {("macro", "CPIAUCSL"), ("macro", "FEDFUNDS"), ("news", "general")} <= _keys(m)
    json.dumps(m)


def test_ingest_continues_past_an_unknown_ticker(tmp_path):
    """A ticker the price provider 404s is a failure; every other ticker is fetched."""
    bad = WL.assets[0].ticker
    missing = ProviderUnavailable("tiingo", "tiingo/daily", 404, "request rejected")
    registry, _ = _registry(
        tmp_path, _routes({f"api.tiingo.com/tiingo/daily/{bad}/": missing})
    )

    m = run_ingest(registry, WL, now=NOW)

    assert [(f["kind"], f["key"]) for f in m["failures"]] == [("prices", bad)]
    others = {a.ticker for a in WL.assets[1:]}
    assert others <= {key for kind, key in _keys(m) if kind == "prices"}
    assert ("prices", bad) not in _keys(m)


def test_failure_messages_mask_credentials(tmp_path):
    """A transport error quoting the request URL must not checkpoint the key."""
    leak = ProviderUnavailable(
        "?", "", None, "Max retries exceeded with url: /fred?series_id=GDP&api_key=s3cret"
    )
    registry, _ = _registry(tmp_path, _routes({"api.stlouisfed.org|series_id=GDP": leak}))

    m = run_ingest(registry, WL, now=NOW)

    assert "s3cret" not in json.dumps(m)
    assert "api_key=***" in m["failures"][0]["error"]


def test_an_unrouted_request_propagates(tmp_path):
    """A missing canned response fails the test run instead of becoming a failure entry."""
    routes = _routes()
    del routes["finnhub.io|category=general"]
    registry, _ = _registry(tmp_path, routes)

    with pytest.raises(UnroutedRequest):
        run_ingest(registry, WL, now=NOW)


@pytest.mark.parametrize("error", [RegistryError("no adapter registered"), TypeError("a bug")])
def test_a_non_provider_error_propagates(tmp_path, error):
    """Only an unavailable provider is a recorded failure; anything else is a defect."""
    registry, _ = _registry(tmp_path, _routes({"api.stlouisfed.org|series_id=UNRATE": error}))

    with pytest.raises(type(error)):
        run_ingest(registry, WL, now=NOW)


def test_every_fetch_failing_raises(tmp_path):
    """With nothing fetched there is nothing to score, so the step fails."""
    routes = {
        host: ProviderUnavailable("any", "any", 503, "down")
        for host in ("api.stlouisfed.org", "api.tiingo.com", "finnhub.io")
    }
    registry, _ = _registry(tmp_path, routes)

    with pytest.raises(IngestError):
        run_ingest(registry, WL, now=NOW)


def test_manifest_from_store_matches_ingest(tmp_path):
    """The CLI rebuilds the same manifest from the raw store alone."""
    store, m = _ingest(tmp_path)

    rebuilt = manifest_from_store(store, WL, at=NOW)

    assert rebuilt["entries"] == m["entries"]
    assert rebuilt["failures"] == []
    assert rebuilt["at"] == NOW.isoformat()


def test_manifest_from_store_reports_what_is_missing(tmp_path):
    """A watched series with no stored payload is listed as a failure."""
    down = ProviderUnavailable("fred", "series/observations", 503, "server error")
    store, m = _ingest(tmp_path, _routes({"api.stlouisfed.org|series_id=GDP": down}))

    rebuilt = manifest_from_store(store, WL, at=NOW)

    assert _keys(rebuilt) == _keys(m)
    assert [(f["kind"], f["key"]) for f in rebuilt["failures"]] == [("macro", "GDP")]


# ---------------------------------------------------------------------------
# Sentiment
# ---------------------------------------------------------------------------


def test_sentiment_scores_only_matched_articles(tmp_path):
    """Only articles naming a watched asset are scored."""
    store, m = _ingest(tmp_path)

    s = run_sentiment(store, m, WL)

    assert s["articles"] == 3 and s["unmatched"] == 1
    assert {i["payload"]["entity_id"] for i in s["scored"]} == {AAPL}
    assert s["rejected"] == 0
    json.dumps(s)


def test_sentiment_payloads_trace_to_the_raw_payload(tmp_path):
    """VR-7: each payload names its article, its time, and its raw payload."""
    store, m = _ingest(tmp_path)
    news_id = next(e["payload_id"] for e in m["entries"] if e["kind"] == "news")

    s = run_sentiment(store, m, WL)

    by_source = {i["payload"]["source_reference"]: i for i in s["scored"]}
    assert set(by_source) == {"finnhub:news:8614107", "finnhub:news:8614108"}
    headline = by_source["finnhub:news:8614107"]
    summary_only = by_source["finnhub:news:8614108"]
    assert headline["match_weight"] == 1.0
    assert summary_only["match_weight"] == 0.6
    assert headline["payload"]["timestamp"] == datetime.fromtimestamp(
        1791885473, UTC
    ).isoformat()
    for item in s["scored"]:
        assert item["payload"]["provenance"]["payload_id"] == news_id


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_profile_name_does_not_match_unrelated_articles(tmp_path, blank):
    """An empty Finnhub profile name must not link every article to its asset."""
    profile = json.dumps({"name": blank, "currency": "USD"}).encode()
    store, m = _ingest(tmp_path, _routes({f"{PROFILE}|symbol=MSFT": profile}))

    s = run_sentiment(store, m, WL)

    assert {i["payload"]["entity_id"] for i in s["scored"]} == {AAPL}
    assert s["unmatched"] == 1


def test_watch_terms_drop_blank_names_and_tickers():
    """Blank names are dropped; an asset with a blank ticker gets no terms."""
    wl = Watchlist(
        assets=(
            WatchedAsset(AAPL, "AAPL", ("Apple", "  ")),
            WatchedAsset(MSFT, " ", ("Microsoft",)),
            WatchedAsset("equity:NYSE:KO", "KO", ()),
        ),
        macro=(),
    )
    profiles = {"AAPL": AssetProfile("Apple Inc", "USD"), "KO": AssetProfile("", "USD")}

    terms = build_watch_terms(wl, profiles)

    assert terms == [
        WatchTerms(AAPL, "AAPL", ("Apple", "Apple Inc")),
        WatchTerms("equity:NYSE:KO", "KO", ()),
    ]
    assert resolve_entities("Central bank holds rates steady", "Policy unchanged.", terms) == []


# ---------------------------------------------------------------------------
# Graph integration
# ---------------------------------------------------------------------------


def test_graph_integration_writes_nodes_and_edges(tmp_path, db):
    """Every watched asset and series becomes a node; every score targets its asset."""
    _, _, s, repo, out = _integrate(tmp_path, db)

    assert out["assets"] == len(WL.assets) and out["edges"]["TARGETS"] == len(s["scored"])
    assert out["indicators"] == len(WL.macro)
    assert repo.counts()["sentiment_metric"] == out["metrics"] == len(s["scored"])
    json.dumps(out)

    name, currency = db.execute(
        "SELECT name, currency FROM asset WHERE asset_id = ?", [AAPL]
    ).fetchone()
    assert (name, currency) == ("Apple Inc", "USD")
    assert db.execute(
        "SELECT name FROM asset WHERE asset_id = ?", [MSFT]
    ).fetchone() == ("Microsoft",)
    targets = db.execute(
        "SELECT dst_node, weight, confidence, observation_count FROM graph_edge"
        " WHERE rel_type = 'TARGETS' ORDER BY weight"
    ).fetchall()
    assert targets == [(AAPL, 0.6, 0.6, 1), (AAPL, 1.0, 1.0, 1)]


def test_every_metric_has_lineage(tmp_path, db):
    """VR-7: every metric has an observation and a lineage row; every FRED point is stored."""
    store, m, _, repo, _ = _integrate(tmp_path, db)

    counts = repo.counts()
    assert counts["scoring_lineage"] == counts["sentiment_observation"] == counts[
        "sentiment_metric"
    ]
    parsed = sum(
        len(parse_fred_observations(store.get(e["payload_id"]).body))
        for e in m["entries"]
        if e["kind"] == "macro"
    )
    assert parsed > 0 and counts["macro_observation"] == parsed

    news_id = next(e["payload_id"] for e in m["entries"] if e["kind"] == "news")
    lineage = db.execute("SELECT DISTINCT payload_id, model_id FROM scoring_lineage").fetchall()
    assert lineage == [(news_id, "stub@0")]


def test_graph_integration_is_idempotent(tmp_path, db):
    """Running the same step twice leaves the graph unchanged."""
    store, m, s, repo, first = _integrate(tmp_path, db)
    before = repo.counts()

    second = run_graph_integration(store, repo, m, s, WL, at=NOW)

    assert repo.counts() == before
    assert second == first


def test_graph_integration_takes_every_timestamp_from_at(tmp_path, db):
    """R4: no written timestamp comes from the wall clock."""
    _integrate(tmp_path, db)
    at = db.execute("SELECT CAST(? AS TIMESTAMP)", [NOW]).fetchone()[0]

    for sql in (
        "SELECT DISTINCT updated_at FROM asset",
        "SELECT DISTINCT updated_at FROM macro_indicator",
        "SELECT DISTINCT created_at FROM sentiment_metric",
        "SELECT DISTINCT computed_at FROM graph_edge",
    ):
        assert db.execute(sql).fetchall() == [(at,)], sql


def _trading_days(start: date, end: date) -> list[date]:
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return [d for d in days if d.weekday() < 5]


def _tiingo(days: list[date], base: float, phase: float, drift: float) -> bytes:
    rows = []
    for i, d in enumerate(days):
        close = base * math.exp(0.02 * math.sin(0.7 * i + phase) + drift * i)
        rows.append({"date": f"{d.isoformat()}T00:00:00.000Z", "adjClose": close})
    return json.dumps(rows).encode()


def _fred_monthly(first: date, count: int) -> bytes:
    rows = []
    for i in range(count):
        month = first.month - 1 + i
        observed = date(first.year + month // 12, month % 12 + 1, 1)
        nxt = month + 1
        released = date(first.year + nxt // 12, nxt % 12 + 1, 12)
        value = 320.0 + 0.3 * i + 0.4 * (-1) ** i + 0.1 * math.sin(i)
        rows.append(
            {
                "realtime_start": released.isoformat(),
                "realtime_end": "9999-12-31",
                "date": observed.isoformat(),
                "value": f"{value:.3f}",
            }
        )
    return json.dumps({"observations": rows}).encode()


def test_graph_integration_derives_correlation_and_influence_edges(tmp_path, db):
    """Long enough series yield CORRELATES_WITH and INFLUENCES edges."""
    days = _trading_days(date(2026, 1, 2), date(2026, 10, 16))
    routes = _routes(
        {
            "api.tiingo.com/tiingo/daily/AAPL/": _tiingo(days, 100.0, 0.0, 0.001),
            "api.tiingo.com/tiingo/daily/MSFT/": _tiingo(days, 200.0, 0.3, 0.0005),
            "api.stlouisfed.org": b'{"observations": []}',
            "api.stlouisfed.org|series_id=CPIAUCSL": _fred_monthly(date(2025, 12, 1), 10),
        }
    )

    _, _, _, _, out = _integrate(tmp_path, db, routes)

    assert out["edges"]["CORRELATES_WITH"] == 1
    assert out["edges"]["INFLUENCES"] == 2
    correlated = edges_from(db, AAPL, rel_type="CORRELATES_WITH")
    assert [e["dst_node"] for e in correlated] == [MSFT]
    influenced = edges_from(db, "FRED:CPIAUCSL", rel_type="INFLUENCES")
    assert sorted(e["dst_node"] for e in influenced) == [AAPL, MSFT]


# ---------------------------------------------------------------------------
# Queries and evaluation
# ---------------------------------------------------------------------------


def _seed(repo: GraphRepository, metric_id: str, score: float, when: datetime) -> None:
    repo.upsert_sentiment_metric(
        SentimentMetric(
            metric_id=metric_id,
            entity_id=MSFT,
            entity_type=NodeType.ASSET,
            score=score,
            confidence=0.7,
            timestamp=when,
            source_reference=f"seed:{metric_id}",
            model_id="stub@0",
            text_hash="0" * 64,
            created_at=when,
        )
    )


def test_sentiment_scores_window_is_half_open(db):
    """Rows with start <= timestamp < end, carrying entity and confidence."""
    repo = GraphRepository(db)
    start, end = NOW - timedelta(days=7), NOW
    _seed(repo, "before", 0.9, start - timedelta(seconds=1))
    _seed(repo, "first", -0.5, start)
    _seed(repo, "last", -0.3, end - timedelta(seconds=1))
    _seed(repo, "at-end", 0.9, end)

    rows = sentiment_scores(db, start=start, end=end)

    assert rows == [
        {"metric_id": "first", "entity_id": MSFT, "score": -0.5, "confidence": 0.7},
        {"metric_id": "last", "entity_id": MSFT, "score": -0.3, "confidence": 0.7},
    ]


def test_evaluation_uses_graph_history(tmp_path, db, monkeypatch):
    """Drift compares this run's scores with the previous week's, excluding this run."""
    monkeypatch.chdir(tmp_path)  # MLflow's default tracking file lands here
    _, _, s, repo, _ = _integrate(tmp_path, db)
    _seed(repo, "ref-1", -0.5, NOW - timedelta(days=2))
    _seed(repo, "ref-2", -0.3, NOW - timedelta(days=3))
    _seed(repo, "too-old", 1.0, NOW - timedelta(days=8))
    current = [i["payload"] for i in s["scored"]]

    result = run_evaluation(db, current, at=NOW)

    evaluation = result["evaluation"]
    assert evaluation["items_scored"] == len(s["scored"])
    mean_current = sum(p["polarity_score"] for p in current) / len(current)
    assert evaluation["drift_score"] == pytest.approx(abs(mean_current - (-0.4)), abs=1e-6)
    assert set(result["mlflow"]) >= {"logged", "reason"}
    json.dumps(result)


def test_evaluation_without_history_reports_no_drift(tmp_path, db, monkeypatch):
    """With no earlier scores there is nothing to drift from."""
    monkeypatch.chdir(tmp_path)
    _, _, s, _, _ = _integrate(tmp_path, db)

    result = run_evaluation(db, [i["payload"] for i in s["scored"]], at=NOW)

    assert result["evaluation"]["items_scored"] == len(s["scored"])
    assert result["evaluation"]["drift_score"] is None
