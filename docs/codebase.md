# Codebase Guide

A tour of the repository for someone about to change it: what lives where, how
the pieces call each other, how the tests are organised, and the conventions the
gates enforce. The [architecture page](architecture.md) explains the design; the
generated [API reference](reference/) documents every public function.

## Repository layout

```text
.
├── src/                    the importable package is literally `src`
│   ├── config/             settings, provider registry, watchlist
│   ├── orchestration/      durable workflow, step functions, retries, compensation
│   ├── ingestion/          adapters, cache, rate limiter, raw payload store, parsers
│   ├── sentiment/          entity matching, analyzer, calibration, payload schema
│   ├── graph/              DuckDB schema, repository, edges, queries, read-only API
│   ├── observability/      spans, metrics, evaluation
│   └── cli/                the `aetheris` command and the licence gate
├── tests/
│   ├── unit/               pure tests, one module each
│   ├── contract/           behaviour pinned by a contract or an external file
│   ├── integration/        whole-pipeline tests with scripted HTTP
│   └── fixtures/           synthetic provider bodies, stub model, a11y fixture
├── scripts/                build, maintenance, and gate tooling
├── docs/                   this site (reference/ and SUMMARY.md are generated)
├── .specify/               Spec Kit: constitution, spec, plans, task breakdowns
├── .github/workflows/      ci.yml (every push and PR), docs.yml (Pages deploy)
├── pyproject.toml          package, extras (dev, docs), ruff, pytest config
├── .importlinter           layer boundary contracts
├── mkdocs.yml · package.json   documentation toolchain
└── .env.example            credential template (.env itself is git-ignored)
```

## `src/config`

| Module | What it does |
|---|---|
| `settings.py` | `Settings`, loaded from a TOML file (`--config` / `AETHERIS_CONFIG`) with environment variables winning; reads a local `.env` (`AETHERIS_ENV_FILE`). Store paths, credentials, retry policy, telemetry and MLflow settings, `dry_run`. |
| `providers.py` | The provider registry: every provider's endpoints, quotas, fallback order, and redistribution terms. The single source of truth for free-tier limits. |
| `watchlist.py` | What the pipeline fetches: `WatchedAsset`, `WatchedSeries`, `Watchlist`, the defaults, and `load_watchlist` for the `[watchlist]` TOML table, validated at load time. |

## `src/orchestration`

| Module | What it does |
|---|---|
| `steps.py` | The pipeline itself, as plain functions with JSON results: `run_ingest`, `manifest_from_store`, `run_sentiment`, `run_graph_integration`, `run_evaluation`, plus `pipeline_providers`. No DBOS import, so the CLI and the tests call the same code. |
| `workflows.py` | Registers the five-step DBOS workflow; each step runs one function from `steps.py` in a worker thread. `start_async`, `run_to_completion`, `resume_to_completion`. |
| `wiring.py` | `build_ingestion_registry`: every provider's adapters registered with credentials from settings. |
| `checkpoints.py` | DBOS launch, configuration, and step-level resume (`rewind_workflow`). |
| `compensation.py` | The step state machine (`PENDING → RUNNING → SUCCEEDED / FAILED → COMPENSATING → COMPENSATED`) and per-step compensators. |
| `retry.py` | The bounded backoff policy, built from settings. |
| `triggers.py` | Schedule and event trigger sources (`aetheris status` lists them). |

## `src/ingestion`

| Module | What it does |
|---|---|
| `base.py` | The adapter boundary: `FetchRequest`, `HttpResponse`, `ProviderUnavailable` (with retryable vs. non-retryable status), the real HTTP client. |
| `fred_client.py`, `market_client.py`, `news_client.py` | Adapters that only shape requests (URL, parameters, auth); they never retry. |
| `registry.py` | `IngestionRegistry.fetch`: provider selection in fallback order, cache check, rate limiting, adapter state (healthy, rate-limited, exhausted), storing the payload with the caller's parameters. |
| `ratelimit.py` | Token buckets, one per quota window per provider, acquired all-or-none. |
| `cache.py` | TTL cache over stored payloads. |
| `payloads.py` | The append-only SQLite raw payload store; `latest_by_endpoint` returns metadata only so bodies are loaded by id when needed. |
| `parsers.py` | Turn the four bodies the pipeline reads into plain records: FRED first releases (dropping rows clipped to the request window), Tiingo adjusted closes, Finnhub profiles and news. |

## `src/sentiment`

| Module | What it does |
|---|---|
| `entities.py` | `resolve_entities`: match a headline and summary to watched assets by ticker and name. |
| `analyzer.py` | `score_text`: polarity and calibrated confidence from the stub model or a manifest checkpoint; raises `TextTooLongError` past a model's context limit. |
| `calibration.py` | Temperature scaling. |
| `models.py` | The licence-checked model manifest (FinSense-ModernBERT, distilroberta-financial, VADER). An unlisted model is refused. |
| `schema.py` | `build_payload` and `validate_payload`: the sentiment JSON contract, metric identity, provenance. |

## `src/graph`

| Module | What it does |
|---|---|
| `schema.py` | DuckDB DDL, node and edge dataclasses with range checks, `GraphDatabase` (UTC session time zone). `get_database` is a process-wide singleton; open `GraphDatabase(path)` directly anywhere a path matters. |
| `repository.py` | `GraphRepository`: idempotent upserts for nodes, edges, macro observations, and sentiment provenance, each with a span. |
| `edges.py` | Availability rules (`close_available_at`, `release_available_at`) and the CORRELATES_WITH and INFLUENCES statistics. |
| `align.py` | The general backward as-of join (`Observation`, `align`); its `Observation` type is used by `edges.py`. |
| `queries.py` | 1–2 hop query builders; every returned datetime is timezone-aware UTC. |
| `api.py` | The read-only, in-process API (`get_entity`, `sentiment_for`, `edges_from`, `top_influencers`, `correlated_with`, `path`, `list_assets`). |

## `src/observability` and `src/cli`

| Module | What it does |
|---|---|
| `observability/telemetry.py` | OpenTelemetry setup and the `span` / `annotate` helpers. |
| `observability/metrics.py` | Metric instruments for fetches, cache, quotas, steps, inference, graph writes, drift. |
| `observability/evaluate.py` | Score summaries, drift against a reference, MLflow logging that degrades gracefully. |
| `cli/main.py` | Every `aetheris` command (see the [CLI reference](usage/cli.md)). |
| `cli/licenses.py` | The Principle I licence gate: SPDX expression parsing, accepted exemptions (amendment A-001), hand-verified licences. |

## Tests

588 tests: 287 unit, 170 contract, 131 integration. None makes a live provider
call.

- **Scripted HTTP.** `tests/integration/fakes.py` provides `RoutedHttpClient`
  (responses routed by URL and parameters) and the fixture routes the pipeline
  tests share; an unrouted request fails loudly.
- **DBOS.** `tests/integration/conftest.py` provides `dbos_env`, a throwaway
  SQLite system database per test.
- **Fixtures** in `tests/fixtures/providers/` are synthetic bodies in the live
  response shapes. Recorded cassettes (`tests/fixtures/cassettes/*.yaml`) stay
  git-ignored because three providers forbid redistributing free-tier data;
  tests that read them pass whether or not they exist.
- **Key suites**: `test_pipeline_steps.py` (every step), `test_pipeline_run.py`
  (the durable workflow, resume without re-fetching), `test_cli_pipeline.py`
  (the CLI on stored data), `test_point_in_time.py` (byte-identical replay, no
  lookahead), `test_edges.py` (statistics and availability), `test_licenses.py`
  (the gate fails closed), `test_ci_workflow.py` (CI properties that once broke).

## Scripts

| Script | Purpose |
|---|---|
| `check_readme_sync.py` | Fails CI when the README states a stale fact, a missing path, a broken anchor, or a "not yet implemented" claim about a finished task. |
| `gen_api.py` | Generates the API reference pages and the site navigation at build time. |
| `fix_built_html.js` | Repairs duplicate landmark labels and missing table-header scopes in the built site. |
| `check_a11y_rendered.js` | The rendered accessibility gate (contrast, heading order) in headless Chromium, with all network requests blocked. |
| `scan_secrets.py` | Refuses to publish a site that contains a credential. |
| `check_licenses.js` | The Node half of the licence gate. |
| `record_cassettes.py` | Records provider cassettes locally (uses real quota; never committed). |
| `audit_fr_coverage.py` | Maps requirements to tasks and tests. |

## Conventions the gates enforce

- **Spec first.** New behaviour starts in `.specify/` (spec clarification or new
  FR, plan, tasks) before code (Principle V).
- **Docstrings on every public name.** The API reference is generated from them,
  and ruff's docstring rules fail the build otherwise.
- **Layer boundaries** per `.importlinter`.
- **Permissive licences only**, including transitive dependencies; on Linux,
  install CPU-only torch so no NVIDIA wheels are pulled in.
- **UTC everywhere.** A naive datetime is an error, not a convenience.
- **No wall clock in what a step writes.** Use the run's `at`.
- **README facts stay true.** Update the test count and any changed claim in the
  same commit.
