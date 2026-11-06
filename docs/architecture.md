# Architecture

This page explains how Aetheris works: what each pipeline step reads and writes,
how the layers are kept apart, how a run survives failure, and the rules that
keep the knowledge graph free of lookahead. For where each piece lives in the
source tree, see the [codebase guide](codebase.md). For what is finished and
what comes next, see the [roadmap](roadmap.md).

## The pipeline at a glance

A scheduled run makes five durable steps. Each step's output is checkpointed,
so a failed run resumes from the step that failed instead of starting over.

```text
 1 trigger ──▶ 2 ingest ──▶ 3 sentiment ──▶ 4 graph integration ──▶ 5 evaluation
   fixes `at`   raw payloads   scored news     nodes, observations,    drift + MLflow
                + manifest     payloads        edges in DuckDB
```

| Step | Reads | Writes | Returns (checkpointed JSON) |
|---|---|---|---|
| 1. Trigger | the clock, once | nothing | the run's as-of time `at`, retry settings |
| 2. Ingest | the watchlist; provider APIs (through the cache) | raw payloads in SQLite | a *manifest*: one entry per payload, keyed by kind and series, ticker, or category, plus any `failures` |
| 3. Sentiment | news payloads named in the manifest | nothing | scored payloads (one per article per matched asset) and counts |
| 4. Graph integration | the manifest's payloads and the scored payloads | the DuckDB knowledge graph | node, observation, and edge counts |
| 5. Evaluation | the run's scores and the graph's previous 7 days | MLflow run (when available), Prometheus metrics | the evaluation summary and drift |

The whole run shares one `at`. The trigger step fixes it, and every later step
reads it back from the trigger's checkpoint, so a run resumed hours later still
writes exactly what it would have written the first time.

The same step functions also back the CLI: `aetheris graph upsert` runs steps 3
and 4 over the payloads already stored, `aetheris score` runs step 3 and prints
the result, and `aetheris evaluate` runs step 5 over the last 24 hours of the
graph.

## Layers and the rules between them

The code is split into five layers plus configuration and the CLI. Import
boundaries are enforced in CI by `import-linter` (`.importlinter`), so a change
that crosses a boundary fails the build rather than drifting in quietly.

| Layer | Package | Responsibility | May not import |
|---|---|---|---|
| Configuration | `src.config` | settings, provider registry, watchlist | any other layer |
| Orchestration | `src.orchestration` | the durable workflow and the step functions that compose the layers | — |
| Ingestion | `src.ingestion` | provider adapters, cache, rate limiter, raw payload store, response parsers | orchestration, sentiment, graph |
| Sentiment | `src.sentiment` | news-to-asset matching, scoring, calibration, payload schema | graph, ingestion |
| Graph | `src.graph` | schema, idempotent upserts, edge statistics, queries, read-only API | ingestion, orchestration |
| Observability | `src.observability` | spans, metrics, evaluation and drift | (leaf layer) |
| CLI | `src.cli` | the `aetheris` command and the licence gate | — |

Only orchestration and the CLI combine layers. That is why provider response
parsing lives in ingestion, entity matching in sentiment, and edge statistics in
graph: each layer works on plain values, and the step functions convert between
them.

## Durable execution

The workflow is registered with [DBOS](https://github.com/dbos-inc/dbos-transact-py)
(MIT), using a SQLite system database next to the raw payload store
(`<raw path>.dbos.sqlite`).

- **One checkpoint per step.** Each step is a `@DBOS.step`; its JSON result is
  stored when it succeeds. Resuming a run (`aetheris run --resume ID`) replays
  the stored results of completed steps and re-executes only from the first
  step without one. Ingest is never repeated once it has succeeded, so a resume
  spends no extra provider quota.
- **Bounded retries.** A failing step is retried with exponential backoff:
  base 1,000 ms, multiplier 2, at most 5 attempts, with jitter, plus a per-step
  activity timeout. These are settings, not constants.
- **Compensation.** When a step is finally exhausted it is marked failed and its
  compensating action runs (`src/orchestration/compensation.py`). Ingestion and
  sentiment are additive, and graph upserts are idempotent, so the next
  successful run completes any partial write.
- **Steps run in worker threads.** Each step body runs its step function with
  `asyncio.to_thread`, opening and closing its own stores, so a blocking call
  never starves DBOS's own timeout handling.

A fetch that fails because a provider is unavailable does not fail the run: the
manifest lists it under `failures`, and the run summary and `aetheris run` output
show it. Any other exception is a defect and fails the step.

## Ingestion

### Providers

Five free-tier providers are declared in `src/config/providers.py`, the single
source of truth for endpoints and quotas.

| Provider | Endpoints | Free-tier quota | Used by the pipeline for |
|---|---|---|---|
| FRED | `series/observations` | 120 / minute | macro series (first releases) |
| Alpha Vantage | `GLOBAL_QUOTE`, `FX_DAILY`, `CRYPTO_RATING` | 25 / day | ad-hoc `aetheris ingest`, fallback |
| Finnhub | `quote`, `stock/profile2`, `/news` | 60 / minute | company profiles, general news |
| Financial Modeling Prep | `quote`, `historical-price-full`, `fmp/articles` | 250 / day | ad-hoc `aetheris ingest`, fallback |
| Tiingo | `tiingo/daily`, `iex`, `tiingo/news` | 50 / hour and 1,000 / day | daily price history |

A live run therefore needs only the FRED, Finnhub, and Tiingo keys.

### What a run fetches

For the default watchlist (8 equities, 5 FRED series) a run makes about 22
requests:

- **Macro**: one FRED request per series, asking for *first releases only*
  (`output_type=4`) over a window starting `max(influence_window_days,
  price_history_days)` days before `at`. FRED caps how many vintage dates one
  request may span, which is why the window is bounded.
- **Prices**: one Tiingo request per ticker for adjusted daily closes from
  `price_history_days` before `at`.
- **Profiles**: one Finnhub `stock/profile2` request per ticker, cached for 7 days.
- **News**: one Finnhub `/news?category=general` request, cached for 15 minutes.

### Rate limiting, caching, fallback

- **Rate limiting** keeps one token bucket per declared quota window
  (minute, hour, day) for each provider, and takes a token from every window or
  from none. Buckets are in memory, so the budget is per process.
- **Caching**: a fetch first checks a TTL cache over stored payloads, so repeated
  requests inside the TTL cost no quota.
- **Fallback**: a data type is served by the first available provider that
  declares the endpoint. A `429` or `5xx` marks the provider rate-limited and
  falls through; `401` and `403` mark it exhausted for the run; a
  request-specific `400`, `404`, or `422` fails only that request.

### The raw payload store

Every response is stored verbatim in SQLite (`aetheris-raw.db` by default),
append-only (VR-8), with its provider, endpoint, request hash, fetch time,
schema version, and the caller's request parameters (never credentials). Later
steps read bodies back by payload id; nothing is re-fetched to recompute the
graph.

## Sentiment

1. **Matching.** Each news article is matched to watched assets before scoring
   (`src/sentiment/entities.py`). A ticker matches case-sensitively as a whole
   word or as a `$TICKER` cashtag; a company name matches case-insensitively as
   a whole phrase (so "Apple's" matches, "Applebee's" does not). A headline match
   gives the link weight 1.0, a summary-only match 0.6. Unmatched articles are
   counted and not scored.
2. **Scoring.** The text `headline + ". " + summary` is scored once per article.
   Polarity is signed, `P(positive) − P(negative)`, and confidence is
   temperature-calibrated (VR-2). Models come from a licence-checked manifest
   (`src/sentiment/models.py`); the pipeline currently uses the deterministic
   stub scorer (see the [roadmap](roadmap.md)).
3. **Payloads.** Each match becomes a sentiment payload validated against the
   schema in `contracts/sentiment-schema.md`, with provenance back to the raw
   payload it came from (VR-7).

An article published after the run's `at` is skipped and counted, so a resumed
run never scores news from its own future.

## The knowledge graph

DuckDB (MIT), one file (`aetheris.duckdb` by default), with its session time zone
pinned to UTC so a file means the same thing on every machine.

| Table | Holds | Identity |
|---|---|---|
| `asset` | tradable instruments | `asset_id` = `asset_class:exchange:symbol` |
| `macro_indicator` | macro series | `indicator_code`, e.g. `FRED:CPIAUCSL` |
| `macro_observation` | every first-release value, with observation and availability times | `(indicator_code, observed_at)` |
| `sentiment_metric` | one scored text per matched asset | `metric_id` (entity, source, text hash, model) |
| `sentiment_observation`, `scoring_lineage` | each metric's times and its raw payload and model (VR-7) | `metric_id` |
| `graph_edge` | weighted, typed edges | `(src_node, dst_node, rel_type, valid_from)` |

Every write is an idempotent upsert (VR-6); sentiment metrics are append-only,
so re-scoring with a different model adds rows rather than overwriting history.

### Edges

| Edge | Direction | Weight | Emitted when |
|---|---|---|---|
| `TARGETS` | metric → asset | match weight (1.0 headline, 0.6 summary) | every scored match |
| `CORRELATES_WITH` | asset ↔ asset (stored `src < dst`) | Pearson r of daily log returns over `correlation_window_days` | at least 20 common returns |
| `INFLUENCES` | indicator → asset | Pearson r between each release's change and the asset's return across that release | at least 8 releases in `influence_window_days` |

Confidence for the derived edges is `1 − p`, with `p` the two-sided p-value of
r from the Fisher z-transform. All weights are clamped to [-1, 1] and all
confidences to [0, 1] (VR-3, VR-4).

## Point-in-time correctness

The binding rule: a value may only influence a graph state once it was
**available**, not merely once the period it describes has passed.

- A **daily close** is available at 16:00 New York time on its trading day
  (`close_available_at`).
- A **macro value** is available at its first-release date and the series'
  configured `release_time` in New York, or at 23:59:59 that day when the time
  is unknown (`release_available_at`). A CPI print for January released on
  12 February is invisible to any state before 12 February.
- **Edges** are derived as of `at` using only values available at or before `at`;
  for INFLUENCES, the "before" close is the last one available at the release and
  the "after" close the first one available after it.
- **Every timestamp written** to the graph comes from `at`, never the wall
  clock, so replaying a run reproduces the graph byte for byte.

`tests/integration/test_point_in_time.py` proves both halves: two replays from
the same stored payloads produce identical tables, and a release dated after `at`
with an extreme value leaves the edges unchanged.

## Evaluation and observability

- **Spans** (OpenTelemetry) wrap every workflow step, fetch, inference, and graph
  write (FR-017); export goes to an OTLP endpoint such as Jaeger when configured.
- **Metrics** are recorded through the OpenTelemetry metrics API
  (`src/observability/metrics.py`, FR-018): fetches, fetch errors, fallbacks,
  cache hits and misses, queue depth, remaining quota, step retries, failures
  and compensations, inference items, out-of-range scores, graph upserts,
  alignment leaks, and the latest drift score. No metrics exporter is configured
  yet; spans export over OTLP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.
- **Evaluation** summarises the run's scores and compares them with the graph's
  previous 7 days for drift, then logs to MLflow when it is available (FR-019).

## Quality gates

| Gate | Enforces | Where |
|---|---|---|
| Licence gate (Python) | every installed distribution is permissively licensed, failing closed on anything unidentified | `aetheris check-licenses --strict` |
| Licence gate (Node) | the docs toolchain's packages are permissive | `node scripts/check_licenses.js` |
| Import contracts | the layer boundaries above | `lint-imports` |
| Lint and docstrings | ruff, including the docstring rules the API reference depends on | `ruff check src/ tests/ scripts/` |
| Tests | unit, contract, and integration suites with no live provider calls | `pytest tests/` |
| README sync | every factual claim in the README still matches the repository | `python scripts/check_readme_sync.py` |
| Documentation gates | docstrings, strict build, HTML repair, secret scan, structural and rendered accessibility | `aetheris docs build` |

CI runs all of them on every push and pull request; the documentation deploy
publishes to GitHub Pages only from `main`, and only after its build passes.
