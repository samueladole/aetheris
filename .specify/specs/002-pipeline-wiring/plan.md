# Pipeline Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `aetheris run` fetch, score, align, upsert, and evaluate real data end to end, and make `graph upsert`, `score`, and `evaluate` operate on stored data instead of placeholders.

**Architecture:** Each pipeline step's logic is a plain function in `src/orchestration/steps.py`, testable without DBOS. The DBOS steps in `workflows.py` call those functions and pass JSON-serialisable manifests between steps. Provider response parsing lives in ingestion, entity matching in sentiment, edge statistics in graph, and the watchlist in config, so the import contracts in `.importlinter` stay unchanged.

**Tech Stack:** Python 3.11, DBOS (SQLite system DB), DuckDB, SQLite, stdlib `math` and `zoneinfo`, pytest with scripted HTTP fakes.

**Spec:** `.specify/specs/spec.md` (FR-001–FR-021, §3–§4, plus the 2026-10-19 clarifications added in Task 1) and `.specify/specs/data-model.md`.

## Global Constraints

- No new runtime dependencies (Principle I). Statistics use stdlib `math`.
- `.importlinter` is unchanged. Only `src.orchestration` and `src.cli` import across ingestion, sentiment, and graph.
- Every datetime is timezone-aware UTC; a naive datetime is an error (VR-5).
- Edge `weight ∈ [-1.0, 1.0]`, `confidence ∈ [0.0, 1.0]` (VR-3, VR-4). `CORRELATES_WITH` uses canonical ordering `src_node < dst_node`.
- Tests make no live provider calls (invariant A6). CI fixtures are synthetic JSON in documented response shapes; recorded cassettes stay git-ignored (provider redistribution terms).
- DBOS step return values must be JSON-serialisable.
- Retry policy unchanged: `base_delay_ms=1000`, `multiplier=2`, `max_attempts=5`, jitter.
- Every task ends with `ruff check src/ tests/ scripts/`, `lint-imports`, and the full `pytest tests/` green. When a task closes a README "Known gaps" entry or changes the test count, it updates the README in the same commit so `python scripts/check_readme_sync.py` passes.

## Review Focus

1. **A macro release with no surrounding prices** (a release before the first close in the window, or on a weekend with no later close yet) is skipped, not crashed and not paired with a wrong close. Test in Task 5.
2. **FRED missing values (`"."`) and repeated dates** are dropped by the parser, never turned into `0.0` or duplicate observations. Test in Task 3.
3. **Ticker false positives**: `KO` must not match inside `KOREA` or `Kodak`, and an article naming no watchlist asset produces no metric. Test in Task 4.
4. **One provider down for one series** leaves the rest of the run intact and records the failure; every fetch failing raises so DBOS retries and compensates. Test in Task 6.
5. **Re-running on identical inputs** leaves the graph byte-identical: no duplicate nodes or edges, and `computed_at` comes from the run time, not the wall clock. Test in Task 9.

---

### Task 1: Record the decisions and add the watchlist

**Files:**
- Modify: `.specify/specs/spec.md` (add `### Session 2026-10-19` under Clarifications)
- Modify: `.specify/specs/data-model.md` (§2.1–§2.3 derivation rules, §3 availability rules, §4 `request_params`)
- Create: `src/config/watchlist.py`
- Test: `tests/unit/test_watchlist.py`

**Interfaces:**
- Produces:
  - `WatchedAsset(asset_id: str, ticker: str, names: tuple[str, ...])` (frozen dataclass)
  - `WatchedSeries(series_id: str, name: str, unit: str, frequency: str, seasonal_adjustment: str, release_time: datetime.time | None)` (frozen)
  - `Watchlist(assets: tuple[WatchedAsset, ...], macro: tuple[WatchedSeries, ...], price_history_days: int = 730, correlation_window_days: int = 126, influence_window_days: int = 730)` (frozen)
  - `DEFAULT_WATCHLIST: Watchlist`
  - `load_watchlist(config_path: str | None) -> Watchlist`: reads the `[watchlist]` table from the same TOML file `Settings.load` reads, falling back to `DEFAULT_WATCHLIST`; raises `src.config.settings.ConfigError` on invalid entries.
  - `WatchedSeries.indicator_code` property → `f"FRED:{series_id}"`

Clarifications to record (one bullet each, Q → A form):
- Universe: a configured watchlist; the defaults are below.
- FRED requests use `output_type=4`, `realtime_start=1776-07-04`, `realtime_end=9999-12-31`, so each observation's `realtime_start` is its first-release date (verified live 2026-10-19: January 2026 CPI → 2026-02-13).
- Availability: an observation is available at its release date plus `release_time` in America/New_York, or at 23:59:59 America/New_York that day when `release_time` is unset. A daily close is available at 16:00 America/New_York on its trading day.
- Price history comes from Tiingo `tiingo/daily` at `/tiingo/daily/{ticker}/prices` (verified live on the free tier 2026-10-19). FMP `historical-price-full` is a dead legacy endpoint.
- News entity matching: tickers case-sensitive as a whole word or `$TICKER`, names case-insensitive as a whole phrase. Headline match → TARGETS weight 1.0, summary-only → 0.6. Unmatched articles are counted and not scored.
- `CORRELATES_WITH`: Pearson r of daily log returns over `correlation_window_days`, emitted when n ≥ 20.
- `INFLUENCES`: release event study. For each release in `influence_window_days`: the indicator's change from the previous release versus the asset return from the last close available at or before the release to the first close available after it. Pearson r across releases, emitted when there are ≥ 8 releases.
- Edge confidence: `1 − p`, where p is the two-sided p-value of r from the Fisher z-transform. TARGETS confidence equals its weight.

Default watchlist (exact values):

| asset_id | ticker | names |
|---|---|---|
| `equity:NASDAQ:AAPL` | AAPL | Apple |
| `equity:NASDAQ:MSFT` | MSFT | Microsoft |
| `equity:NASDAQ:NVDA` | NVDA | Nvidia |
| `equity:NASDAQ:AMZN` | AMZN | Amazon |
| `equity:NYSE:JPM` | JPM | JPMorgan |
| `equity:NYSE:XOM` | XOM | Exxon |
| `equity:NYSE:JNJ` | JNJ | Johnson & Johnson |
| `equity:NYSE:KO` | KO | Coca-Cola |

| series_id | name | unit | frequency | SA | release_time |
|---|---|---|---|---|---|
| GDP | Gross Domestic Product | USD billions | quarterly | SA | 08:30 |
| CPIAUCSL | Consumer Price Index for All Urban Consumers | index | monthly | SA | 08:30 |
| UNRATE | Unemployment Rate | percent | monthly | SA | 08:30 |
| FEDFUNDS | Federal Funds Effective Rate | percent | monthly | NSA | — |
| DGS10 | 10-Year Treasury Constant Maturity Rate | percent | daily | NSA | — |

- [ ] **Step 1: Write the failing tests**

```python
def test_default_watchlist_shape():
    assert len(DEFAULT_WATCHLIST.assets) == 8
    assert [s.series_id for s in DEFAULT_WATCHLIST.macro] == ["GDP", "CPIAUCSL", "UNRATE", "FEDFUNDS", "DGS10"]
    assert DEFAULT_WATCHLIST.macro[1].release_time == time(8, 30)
    assert DEFAULT_WATCHLIST.macro[3].release_time is None

def test_missing_config_uses_defaults():
    assert load_watchlist(None) == DEFAULT_WATCHLIST

def test_toml_watchlist_overrides(tmp_path):
    path = tmp_path / "a.toml"
    path.write_text('[watchlist]\ncorrelation_window_days = 60\n'
                    '[[watchlist.assets]]\nasset_id = "equity:NYSE:KO"\nticker = "KO"\nnames = ["Coca-Cola"]\n')
    wl = load_watchlist(str(path))
    assert [a.ticker for a in wl.assets] == ["KO"]
    assert wl.correlation_window_days == 60
    assert wl.macro == DEFAULT_WATCHLIST.macro   # an omitted table keeps its default

@pytest.mark.parametrize("bad", ["AAPL", "stock:NASDAQ:AAPL", "equity:NASDAQ:"])
def test_invalid_asset_id_is_rejected(tmp_path, bad): ...   # raises ConfigError

def test_invalid_release_time_is_rejected(tmp_path): ...     # release_time = "8.30" -> ConfigError
```

- [ ] **Step 2: Run them and confirm they fail** — `pytest tests/unit/test_watchlist.py -v` → ImportError on `src.config.watchlist`.
- [ ] **Step 3: Implement `src/config/watchlist.py`** with the interfaces above. `asset_id` must match `^(equity|forex|crypto|index):[A-Z0-9.]+:[A-Z0-9.\-]+$`. `release_time` is `"HH:MM"`. Use `tomllib`, and import nothing from other `src` layers except `src.config.settings.ConfigError`.
- [ ] **Step 4: Write the spec and data-model text** listed above.
- [ ] **Step 5: Run the tests and confirm they pass**, then `lint-imports`.
- [ ] **Step 6: Commit** — `feat(config): add the pipeline watchlist and record wiring decisions`.

---

### Task 2: Raw payloads record their request parameters

A raw payload records a request hash but not its parameters, and neither FRED nor Tiingo responses name their series or ticker. Later steps need both.

**Files:**
- Modify: `src/ingestion/payloads.py`, `src/ingestion/registry.py`
- Test: `tests/unit/test_payloads.py` (extend)

**Interfaces:**
- Produces:
  - `RawPayload.request_params: dict[str, str] | None` (new last field, default `None`)
  - `RawPayloadStore.store(..., request_params: Mapping[str, str] | None = None)`
  - `RawPayloadStore.latest_by_endpoint(provider: str, endpoint: str) -> list[RawPayload]`: the most recent payload per `request_hash`, ordered by `fetched_at` descending.
- `IngestionRegistry.fetch` passes the **caller's** `params` (never the adapter-merged params, which carry credentials) to `store`.

- [ ] **Step 1: Write the failing tests**

```python
def test_request_params_round_trip(tmp_path):
    store = RawPayloadStore(str(tmp_path / "raw.db"))
    p, _ = store.store(provider="fred", endpoint="series/observations", request_hash="h",
                       body=b"{}", request_params={"series_id": "GDP"})
    assert store.get(p.payload_id).request_params == {"series_id": "GDP"}

def test_existing_database_gains_the_column(tmp_path):
    # Create raw_payload with the pre-change schema via sqlite3, insert one row,
    # then open RawPayloadStore on it: the old row reads back with request_params None.

def test_latest_by_endpoint_returns_newest_per_request(tmp_path): ...
    # two fetches of hash "a" (older, newer) + one of hash "b" -> 2 payloads, newer "a" kept

def test_registry_never_persists_credentials(tmp_path):
    # Fetch finnhub "quote" through IngestionRegistry with a scripted HTTP client and
    # MarketAdapter("finnhub", api_key="secret-key-123456"); the stored payload's
    # request_params == {"symbol": "AAPL"} and "secret-key-123456" is not in any value.
```

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.** Store params as sorted-key JSON in a nullable `request_params TEXT` column. On open, add the column with `ALTER TABLE raw_payload ADD COLUMN request_params TEXT` when `PRAGMA table_info` lacks it. Adding a column mutates no row, so VR-8 holds.
- [ ] **Step 4: Run the full suite and confirm it passes.**
- [ ] **Step 5: Commit** — `feat(ingestion): record caller request parameters with raw payloads`.

---

### Task 3: Correct the FRED and Tiingo requests, and parse responses

**Files:**
- Modify: `src/ingestion/fred_client.py`, `src/ingestion/market_client.py`, `scripts/record_cassettes.py`
- Create: `src/ingestion/parsers.py`
- Create: `tests/fixtures/providers/fred_cpiaucsl.json`, `tiingo_aapl_prices.json`, `finnhub_profile_aapl.json`, `finnhub_news_general.json` (synthetic, in the live shapes shown in this plan)
- Test: `tests/unit/test_parsers.py`, `tests/unit/test_adapters.py` (create, or extend if one exists)

**Interfaces:**
- Produces (frozen dataclasses and functions in `src/ingestion/parsers.py`):
  - `ParseError(ValueError)`
  - `MacroPoint(observed_on: date, released_on: date, value: float)`
  - `PricePoint(trading_day: date, adj_close: float)`
  - `AssetProfile(name: str, currency: str)`
  - `NewsItem(article_id: str, published_at: datetime, headline: str, summary: str, source: str)`
  - `parse_fred_observations(body: bytes) -> list[MacroPoint]`
  - `parse_tiingo_prices(body: bytes) -> list[PricePoint]`
  - `parse_finnhub_profile(body: bytes) -> AssetProfile | None` (`None` for `{}`, which Finnhub returns for unknown symbols)
  - `parse_finnhub_news(body: bytes) -> list[NewsItem]`
- `FredAdapter.build_request` defaults `output_type="4"`, `realtime_start="1776-07-04"`, `realtime_end="9999-12-31"`; caller params override.
- `MarketAdapter("tiingo")` for `tiingo/daily` builds `https://api.tiingo.com/tiingo/daily/{ticker}/prices`, keeping `startDate` as a query parameter.

Live shapes (verified 2026-10-19):
- FRED: `{"observations": [{"realtime_start": "2026-02-13", "realtime_end": "9999-12-31", "date": "2026-01-01", "value": "326.588"}]}`
- Tiingo: `[{"date": "2026-09-01T00:00:00.000Z", "close": 325.13, "adjClose": 325.13, ...}]`
- Finnhub news: `[{"id": 8614107, "datetime": 1791885473, "headline": "...", "summary": "...", "source": "CNBC", "related": "", ...}]`
- Finnhub profile2: `{"ticker": "AAPL", "name": "Apple Inc", "currency": "USD", "exchange": "NASDAQ NMS - GLOBAL MARKET", ...}`

- [ ] **Step 1: Write the failing tests**

```python
def test_fred_released_on_is_realtime_start():
    pts = parse_fred_observations(FIXTURE("fred_cpiaucsl.json"))
    assert pts[0] == MacroPoint(date(2026, 1, 1), date(2026, 2, 13), 326.588)

def test_fred_missing_values_and_repeats_are_dropped():
    body = b'{"observations": [{"realtime_start":"2026-02-13","date":"2026-01-01","value":"."},' \
           b'{"realtime_start":"2026-02-13","date":"2026-01-01","value":"1.0"},' \
           b'{"realtime_start":"2026-02-14","date":"2026-01-01","value":"2.0"}]}'
    assert parse_fred_observations(body) == [MacroPoint(date(2026,1,1), date(2026,2,13), 1.0)]   # first release wins

def test_tiingo_uses_adjusted_close(): ...       # PricePoint(date(2026,9,1), 325.13)
def test_news_time_is_utc(): ...                 # published_at == datetime.fromtimestamp(1791885473, UTC)
def test_unknown_profile_is_none(): ...          # parse_finnhub_profile(b"{}") is None

@pytest.mark.parametrize("body", [b"not json", b'{"Error Message": "Legacy Endpoint"}', b'{"detail": "Not found."}'])
def test_error_bodies_raise_parse_error(body): ...   # every parser raises ParseError on these

def test_fred_requests_initial_release_dates():
    req = FredAdapter(api_key="k").series_request("GDP")
    assert (req.params["output_type"], req.params["realtime_start"]) == ("4", "1776-07-04")

def test_tiingo_prices_path():
    req = MarketAdapter("tiingo", api_key="k").build_request("tiingo/daily", {"tickers": "AAPL", "startDate": "2024-10-19"})
    assert req.url == "https://api.tiingo.com/tiingo/daily/AAPL/prices"
    assert req.params["startDate"] == "2024-10-19"
```

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement the parsers and the two request changes.** Parsers use only `json` and `datetime`.
- [ ] **Step 4: Update `scripts/record_cassettes.py`** to record FRED with the new parameters and `tiingo/daily` for AAPL. Run it locally once (approved 2026-10-19), and confirm `parse_fred_observations` and `parse_tiingo_prices` accept the recorded bodies. The cassettes stay git-ignored.
- [ ] **Step 5: Run the full suite and confirm it passes.**
- [ ] **Step 6: Commit** — `feat(ingestion): request FRED first-release dates, fix Tiingo prices, add parsers`.

---

### Task 4: Match news to watchlist assets

**Files:**
- Create: `src/sentiment/entities.py`
- Test: `tests/unit/test_entities.py`

**Interfaces:**
- Produces:
  - `WatchTerms(entity_id: str, ticker: str, names: tuple[str, ...])` (frozen)
  - `EntityMatch(entity_id: str, weight: float)` (frozen)
  - `resolve_entities(headline: str, summary: str, terms: Sequence[WatchTerms]) -> list[EntityMatch]`: at most one match per entity (headline weight wins), sorted by `entity_id`. Headline match → `1.0`, summary-only → `0.6`.
- The sentiment layer must not import config or ingestion, so the caller converts `WatchedAsset` to `WatchTerms`.

- [ ] **Step 1: Write the failing tests**

```python
AAPL = WatchTerms("equity:NASDAQ:AAPL", "AAPL", ("Apple",))
KO = WatchTerms("equity:NYSE:KO", "KO", ("Coca-Cola",))

def test_headline_beats_summary():
    assert resolve_entities("Apple beats estimates", "AAPL rose", [AAPL]) == [EntityMatch("equity:NASDAQ:AAPL", 1.0)]

def test_summary_only_match():
    assert resolve_entities("Markets rally", "Shares of $AAPL rose", [AAPL]) == [EntityMatch("equity:NASDAQ:AAPL", 0.6)]

@pytest.mark.parametrize("text", ["KOREA exports rise", "Kodak files", "a ko punch"])
def test_ticker_needs_a_whole_word_in_its_own_case(text):
    assert resolve_entities(text, "", [KO]) == []

def test_names_are_case_insensitive_whole_phrases():
    assert resolve_entities("COCA-COLA raises dividend", "", [KO])[0].weight == 1.0
    assert resolve_entities("Applebee's expands", "", [AAPL]) == []

def test_several_entities_sorted(): ...    # both AAPL and KO matched -> sorted by entity_id
def test_no_match(): ...                   # -> []
```

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement with `re`.** Ticker pattern: `(?<![A-Za-z0-9])\$?{TICKER}(?![A-Za-z0-9])`, case-sensitive. Name pattern: `(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9'])`, with `re.IGNORECASE`.
- [ ] **Step 4: Run the tests and confirm they pass**, then `lint-imports`.
- [ ] **Step 5: Commit** — `feat(sentiment): match news text to watchlist assets`.

---

### Task 5: Derive CORRELATES_WITH and INFLUENCES edges

**Files:**
- Create: `src/graph/edges.py`
- Test: `tests/unit/test_edges.py`

**Interfaces:**
- Consumes: `src.graph.align.Observation`, `first_available_at_or_before`; `src.graph.schema.GraphEdge`, `RelationType`.
- Produces:
  - `pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None` (`None` when n < 3 or either variance is 0)
  - `significance(r: float, n: int) -> float`: `1 − p`, where p is the two-sided p-value from the Fisher z-transform. Returns `0.0` when n < 4.
  - `close_available_at(trading_day: date) -> datetime`: 16:00 America/New_York on that day, in UTC.
  - `correlation_edges(prices: Mapping[str, Sequence[Observation]], *, at: datetime, window_days: int, min_observations: int = 20) -> list[GraphEdge]`
  - `influence_edges(macro: Mapping[str, Sequence[Observation]], prices: Mapping[str, Sequence[Observation]], *, at: datetime, window_days: int, min_releases: int = 8) -> list[GraphEdge]`
  - Price observations: `value` is the adjusted close, `observed_at` is 00:00 UTC on the trading day, and `available_at` is `close_available_at(day)`. Macro observations: `value` is the float level, `observed_at` the period start, `available_at` the release time from Task 1. Keys are `asset_id` and `indicator_code`.
  - Every emitted edge has `valid_from = at − window_days`, `valid_to = at`, and `computed_at = at` (never the wall clock), `observation_count = n`.

Algorithms the signatures leave open:

```text
significance(r, n):
    if n < 4: return 0.0
    z = atanh(clamp(r, -0.999999, 0.999999)) * sqrt(n - 3)
    p = erfc(abs(z) / sqrt(2))
    return clamp(1 - p, 0.0, 1.0)

correlation_edges:
    keep each series' observations with valid_from <= available_at <= at
    for each pair (a, b) with a < b:
        days = trading days present in both, ascending
        returns = log(close[d] / close[d_prev]) for consecutive days in `days`
        r = pearson(ret_a, ret_b); n = len(returns)
        emit CORRELATES_WITH(a -> b, weight=r, confidence=significance(r, n)) when r is not None and n >= min_observations

influence_edges:
    for each indicator, sort its observations by available_at and keep those with valid_from <= available_at <= at
    for each consecutive release pair (prev, cur):
        delta = cur.value - prev.value
        for each asset:
            before = last close with available_at <= cur.available_at
            after  = first close with cur.available_at < available_at <= at
            if both exist: record (delta, after.value / before.value - 1)
    r = pearson(deltas, returns); n = len(pairs)
    emit INFLUENCES(indicator -> asset, weight=r, confidence=significance(r, n)) when r is not None and n >= min_releases
```

- [ ] **Step 1: Write the failing tests**

```python
def test_significance_bounds():
    assert significance(0.0, 100) == pytest.approx(0.0, abs=1e-9)
    assert significance(0.9, 100) > 0.999
    assert significance(0.9, 3) == 0.0

def test_identical_series_correlate_fully(): ...          # weight ≈ 1.0, confidence > 0.99, n == 29 for 30 closes
def test_canonical_ordering(): ...                        # every edge has src_node < dst_node
def test_too_few_observations_emit_nothing(): ...         # 10 closes, min_observations=20 -> []
def test_close_after_at_is_not_seen():
    # 30 closes; at = day 29 15:59 New York -> day 29's close (16:00) is excluded, so n == 28
def test_inverse_influence_is_negative(): ...             # rising indicator, falling next-day returns -> weight < 0
def test_release_without_a_later_close_is_skipped():
    # last release is after the final close -> that release contributes nothing; no exception
def test_release_before_first_close_is_skipped(): ...
def test_fewer_than_min_releases_emit_nothing(): ...      # 7 releases -> []
def test_edges_are_deterministic():
    assert influence_edges(m, p, at=AT, window_days=730) == influence_edges(m, p, at=AT, window_days=730)
```

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement `src/graph/edges.py`** per the interfaces and algorithms above. Use stdlib `math` and `zoneinfo` only.
- [ ] **Step 4: Run the tests and confirm they pass**, then `lint-imports`.
- [ ] **Step 5: Commit** — `feat(graph): derive correlation and influence edges`.

---

### Task 6: Pipeline step functions

**Files:**
- Create: `src/orchestration/wiring.py`, `src/orchestration/steps.py`
- Modify: `src/graph/queries.py` (add `sentiment_scores`), `src/graph/repository.py` (write the observation and lineage tables, which the schema defines and no code yet writes), `src/cli/main.py` (`_cmd_ingest` uses `build_ingestion_registry`; delete `_build_adapters`)
- Create: `tests/integration/fakes.py` (routed HTTP fake)
- Test: `tests/integration/test_pipeline_steps.py`

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces:
  - `build_ingestion_registry(settings: Settings, *, http: HttpClient | None = None) -> IngestionRegistry`: every provider's adapters registered, with credentials from settings.
  - `IngestError(RuntimeError)`
  - `run_ingest(registry: IngestionRegistry, watchlist: Watchlist, *, now: datetime) -> dict`, returning
    `{"at": iso, "entries": [{"kind": "macro"|"prices"|"profile"|"news", "key": str, "payload_id": str}], "failures": [{"kind": str, "key": str, "error": str}]}`.
    Fetches (data type, endpoint, params):
    - macro: `("macro", "series/observations", {"series_id": s})`
    - prices: `("prices", "tiingo/daily", {"tickers": t, "startDate": (now − price_history_days).date().isoformat()})`
    - profile: `("fundamentals", "stock/profile2", {"symbol": t})` with `ttl_seconds=7 * 86400`
    - news: `("news", "/news", {"category": "general"})` with `ttl_seconds=900`

    A fetch that raises is recorded in `failures` and the step continues. Raises `IngestError` when `entries` is empty.
  - `manifest_from_store(store: RawPayloadStore, watchlist: Watchlist, *, at: datetime) -> dict`: the same shape as `run_ingest`'s result, built from `latest_by_endpoint` and `request_params`, for the CLI.
  - `run_sentiment(store: RawPayloadStore, manifest: dict, watchlist: Watchlist, *, model_id: str = "stub", temperature: float = 1.0) -> dict`, returning
    `{"scored": [{"payload": <build_payload dict>, "match_weight": float}], "articles": int, "unmatched": int, "rejected": int}`.
    Scores `headline + ". " + summary`. `timestamp` is `published_at`, `source_reference` is `f"finnhub:news:{article_id}"`, and `Provenance.payload_id` is the raw payload id (VR-7). Watch terms combine each asset's configured names with its profile name, where one exists. A `ValidationError` increments `rejected`.
  - `run_graph_integration(store: RawPayloadStore, repo: GraphRepository, manifest: dict, sentiment: dict, watchlist: Watchlist, *, at: datetime) -> dict`, returning
    `{"assets": int, "indicators": int, "metrics": int, "edges": {"TARGETS": int, "CORRELATES_WITH": int, "INFLUENCES": int}}`.
    Upserts:
    - assets: name from the profile, else the first configured name; currency from the profile, else `"USD"`
    - indicators: from the watchlist, with `provider="fred"`
    - one SentimentMetric per scored item
    - TARGETS (metric → asset): `weight = confidence = match_weight`, `valid_from = metric timestamp`, `observation_count = 1`
    - the Task 5 edges, built from parsed observations
    - one `macro_observation` row per parsed FRED point, and one `sentiment_observation` plus one `scoring_lineage` row per metric (VR-7)
  - `GraphRepository.upsert_macro_observation(indicator_code: str, observation: Observation) -> None`: idempotent on `(indicator_code, observed_at)`.
  - `GraphRepository.record_sentiment_provenance(metric_id: str, *, payload_id: str, text_hash: str, model_id: str, observed_at: datetime, available_at: datetime) -> None`: writes `sentiment_observation` and `scoring_lineage`, idempotent on `metric_id`.
  - `sentiment_scores(db: GraphDatabase, *, start: datetime, end: datetime) -> list[dict]` in `src/graph/queries.py`, returning `{"metric_id": str, "score": float}` rows with `start <= timestamp < end`.
  - `run_evaluation(db: GraphDatabase, sentiment: dict, *, at: datetime, reference_days: int = 7, drift_threshold: float = 0.1) -> dict`, returning
    `{"evaluation": Evaluation.as_dict(), "mlflow": status}`.
    The reference is `sentiment_scores` over `[at − reference_days, at)`, excluding this run's `metric_id`s.
  - `tests/integration/fakes.py`: `RoutedHttpClient(routes: Mapping[str, bytes | Exception])` with `.calls: list[str]`. A route key is a URL substring plus optional `?k=v` params, for example `"api.stlouisfed.org|series_id=CPIAUCSL"`. It returns `HttpResponse(200, body, {})`, or raises the queued exception.

- [ ] **Step 1: Write the failing tests** (in `tests/integration/test_pipeline_steps.py`, using `tmp_path` stores and the Task 3 fixtures served through `RoutedHttpClient`)

```python
def test_ingest_manifest_covers_the_watchlist(...):
    m = run_ingest(registry, WL, now=NOW)
    assert {(e["kind"], e["key"]) for e in m["entries"]} >= {("macro", "CPIAUCSL"), ("prices", "AAPL"), ("profile", "AAPL"), ("news", "general")}
    assert m["failures"] == []
    json.dumps(m)                                              # DBOS-serialisable

def test_one_failed_series_is_recorded_and_the_rest_continue(...): ...   # route for UNRATE raises ProviderUnavailable
def test_every_fetch_failing_raises(...): ...                            # all routes raise -> IngestError

def test_sentiment_scores_only_matched_articles(...):
    s = run_sentiment(store, m, WL)
    assert s["articles"] == 3 and s["unmatched"] == 1
    assert {i["payload"]["entity_id"] for i in s["scored"]} == {"equity:NASDAQ:AAPL"}

def test_graph_integration_writes_nodes_and_edges(...):
    out = run_graph_integration(store, repo, m, s, WL, at=NOW)
    assert out["assets"] == len(WL.assets) and out["edges"]["TARGETS"] == len(s["scored"])
    assert repo.counts()["sentiment_metric"] == out["metrics"]

def test_every_metric_has_lineage(...):                  # VR-7
    counts = repo.counts()
    assert counts["scoring_lineage"] == counts["sentiment_observation"] == counts["sentiment_metric"]
    assert counts["macro_observation"] == number of parsed FRED points across the manifest

def test_graph_integration_is_idempotent(...): ...       # run twice -> repo.counts() unchanged
def test_manifest_from_store_matches_ingest(...): ...     # same {(kind, key)} set as run_ingest's manifest
def test_evaluation_uses_graph_history(...): ...          # seed earlier metrics; items_scored == len(scored); drift_score is not None
```

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement `wiring.py`, `steps.py`, `queries.sentiment_scores`, the two repository methods, and `fakes.py`**, and switch `_cmd_ingest` to `build_ingestion_registry`. Each new repository write opens a span, as the existing ones do (FR-017).
- [ ] **Step 4: Run the full suite and confirm it passes**, then `lint-imports`.
- [ ] **Step 5: Commit** — `feat(orchestration): implement the pipeline step functions`.

---

### Task 7: Wire the DBOS workflow

**Files:**
- Modify: `src/orchestration/workflows.py`
- Test: `tests/integration/test_pipeline_run.py`

**Interfaces:**
- Consumes: Task 6.
- Produces:
  - `register_workflow(settings: Settings, *, http: HttpClient | None = None, watchlist: Watchlist | None = None) -> None`
  - `start_async(settings=None, *, wait=False, http=None, watchlist=None)`, and the same keyword arguments on `start` and `run_to_completion`.
  - Step signatures: `ingest(dry_run) -> manifest`, `sentiment(dry_run, manifest) -> sentiment dict`, `graph_integration(dry_run, manifest, sentiment) -> counts`, `evaluate(dry_run, sentiment) -> evaluation dict`. `dry_run` keeps today's zero results, and makes no provider calls.
  - `at` is fixed once per workflow run, in the trigger step's result, so a resumed run reuses it.
  - The workflow result gains `"summary": {"fetched": int, "scored": int, "edges": dict}`.
  - Remove the "Only the orchestration shell is complete" paragraph and the `# US… wires … here` comments.

- [ ] **Step 1: Write the failing tests** (reuse the `dbos_env` fixture pattern from `tests/integration/test_checkpoint_resume.py`)

```python
def test_run_writes_the_graph(dbos_env, tmp_path):
    handle = asyncio.run(run_to_completion(settings, http=routed, watchlist=WL))
    assert handle.status == "SUCCESS"
    assert all(s["state"] == "SUCCEEDED" for s in handle.result["steps"])
    assert handle.result["summary"]["scored"] > 0
    assert GraphRepository(get_database(settings.db_path)).counts()["graph_edge"] > 0

def test_resume_does_not_refetch(dbos_env, tmp_path):
    # Make graph_integration fail once (monkeypatch steps.run_graph_integration to raise on the first call),
    # resume the workflow, and assert routed.calls is unchanged after the resume: ingest's checkpointed output was reused.

def test_dry_run_still_makes_no_calls(dbos_env, tmp_path): ...   # routed.calls == []
```

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement the wiring.** Each DBOS step opens its own `RawPayloadStore` and `GraphRepository` from `settings` and closes them before returning.
- [ ] **Step 4: Run the full suite (including the existing checkpoint, resume, and compensation tests) and confirm it passes.**
- [ ] **Step 5: Commit** — `feat(orchestration): run the real pipeline in the durable workflow`.

---

### Task 8: CLI commands on stored data

**Files:**
- Modify: `src/cli/main.py` (`_cmd_graph_upsert`, `_cmd_score`, `_cmd_evaluate`)
- Test: `tests/integration/test_cli_pipeline.py`

**Interfaces:**
- Consumes: `manifest_from_store`, `run_sentiment`, `run_graph_integration`, `run_evaluation`, `load_watchlist`.
- Behaviour:
  - `aetheris graph upsert`: scores the stored news, then aligns and upserts. Prints the counts dict (as JSON with `--json`). Exits `2` when the store has no payloads, keeping today's message.
  - `aetheris score` with no `--text`: scores stored news and prints payloads. `--entity` filters by `entity_id`. `--text` behaves as before.
  - `aetheris evaluate`: evaluates SentimentMetric rows from the last 24 hours in the graph against the previous 7 days. Prints `evaluated 0 item(s)` and exits `0` on an empty graph. The hard-coded sample sentences are deleted.
  - The stale "task T065" comment in `_cmd_score` is removed.

- [ ] **Step 1: Write the failing tests** (seed the raw store by running `run_ingest` with `RoutedHttpClient`, then call `main([...])` and capture stdout)

```python
def test_graph_upsert_writes_the_graph(...):
    assert main(["--db", db, "--raw", raw, "--json", "graph", "upsert"]) == 0
    assert json.loads(out)["edges"]["TARGETS"] > 0

def test_score_reads_stored_news(...): ...       # no --text; output names equity:NASDAQ:AAPL
def test_evaluate_reads_the_graph(...): ...      # after upsert: items_scored == metrics written
def test_evaluate_on_empty_graph(...): ...       # exit 0, "evaluated 0 item(s)"
```

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite and confirm it passes.**
- [ ] **Step 5: Commit** — `feat(cli): run graph upsert, score, and evaluate on stored data`.

---

### Task 9: Point-in-time replay test, README, and validation

**Files:**
- Create: `tests/integration/test_point_in_time.py`
- Modify: `README.md`, `docs/validation.md`

**Interfaces:**
- Consumes: Tasks 6–7.

- [ ] **Step 1: Write the replay and no-lookahead tests**

```python
def test_replay_is_byte_identical(tmp_path):
    # Run run_ingest -> run_sentiment -> run_graph_integration twice, from the same fixtures
    # with the same `at`, into two fresh DuckDB files. Dump every table ordered by primary key
    # with ORDER BY; the two dumps are equal.

def test_a_release_after_at_is_not_observed(tmp_path):
    # Add a CPI observation released one day after `at`, with an extreme value.
    # The INFLUENCES edges for FRED:CPIAUCSL are identical with and without it.
```

- [ ] **Step 2: Run them.** They should pass if Tasks 5–6 are correct. If either fails, fix the code, not the test.
- [ ] **Step 3: Update the README.**
  - Delete the Known gaps entries this work closes: the durable pipeline shell, nothing writing to the graph, alignment off the write path, `score` reading only `--text`, and the narrow replay test.
  - Rewrite the point-in-time section to point at `tests/integration/test_point_in_time.py`.
  - Update the CLI table, the "That is the design" paragraph, and the Python API note.
  - Update the test count.
  - Add the default watchlist and the `[watchlist]` TOML table under Installation.
- [ ] **Step 4: Run `aetheris run --trigger schedule` once against the live APIs** (approved 2026-10-19). Record its step states, counts, and duration against SC-001 (P95 < 5 minutes) in `docs/validation.md`.
- [ ] **Step 5: Run `python scripts/check_readme_sync.py` and the full suite, and confirm both pass.**
- [ ] **Step 6: Commit** — `test(graph): add the point-in-time replay test; document the wired pipeline`.
