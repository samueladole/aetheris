# Roadmap

Where Aetheris stands, how it got here, and what comes next. The
[architecture page](architecture.md) describes the system as it is; this page
tracks its history and its plans. Requirement IDs (FR-…) and success criteria
(SC-…) refer to `.specify/specs/spec.md`.

## Where things stand

The pipeline runs end to end. A scheduled `aetheris run` ingests the watchlist
from FRED, Tiingo, and Finnhub, scores the news, aligns everything as of one
point in time, upserts it into the DuckDB knowledge graph, and evaluates the
result, as a durable workflow that resumes from the step that failed.

| Measure | Value |
|---|---|
| Specification milestones M1–M6 | complete |
| Pipeline tasks (`.specify/specs/tasks.md`) | 81 / 81 |
| Documentation site tasks (feature 001) | 34 / 34 |
| Pipeline wiring tasks (feature 002) | 9 / 9, plus three hardening fixes |
| Tests | 588 (287 unit, 170 contract, 131 integration), no live provider calls |
| Latest live run (2026-11-04) | 5 / 5 steps succeeded, 22 payloads, 5 scored items, 65 edges, no failures, 7.2 s |

The live runs are single samples, so SC-001 (P95 run under 5 minutes) is met
in every run so far but not yet established as a percentile.

## Milestones achieved

### October 2026 — specification and design (Spec Kit)

The project was built specification-first. The constitution (five principles:
open-source only, decoupled layers, durable execution, observability, spec-driven
rigour), the spec with requirements FR-001 to FR-021, the plan, the
licence-verified research, the data model with validation rules VR-1 to VR-9,
four interface contracts, and an 81-task breakdown in five user stories were all
written before code.

### October 2026 — the five layers (milestones M1–M6)

| Date | Milestone | What landed |
|---|---|---|
| 10 Oct | M1 Setup and foundations | package layout, typed settings, provider registry, licence gate, import contracts |
| 10 Oct | M2 Durable orchestration (US1) | DBOS workflow, step checkpoints, bounded backoff, compensation, triggers |
| 10 Oct | M3 Ingestion (US2) | five provider adapters, shared rate limiter, TTL cache, immutable payload store, fallback |
| 11 Oct | M4 Sentiment (US3) | licence-checked model manifest, analyzer, temperature calibration, payload schema |
| 11 Oct | M5 Knowledge graph (US4) | DuckDB schema, idempotent upserts, as-of alignment, 1–2 hop queries, read-only API and CLI |
| 11 Oct | M6 Observability (US5) | spans on every operation, metrics, evaluation and drift, MLflow logging |
| 11–13 Oct | Polish | FR coverage audit, scale measurement, `.env` credentials, recorded provider cassettes |

### October 2026 — documentation site and CI/CD (feature 001)

A MkDocs site whose API reference and navigation are generated from the source,
behind six gates: docstring coverage, strict build, built-HTML repair, a secret
scan, and structural and rendered accessibility checks. It publishes to GitHub
Pages from `main`, and only after its build passes.

### 19 October 2026 — CI restored

Every layer existed, but CI had been red since 10 October and several tasks were
checked off without their deliverables. This release fixed each failure at its
root: the npm lockfile was git-ignored, Linux installs pulled in proprietary CUDA
wheels, the CI docs job had drifted from the canonical gate sequence, and test
fixtures never reached CI. It also made the licence gate fail closed on
licences it cannot identify, added a README accuracy check to CI, and enabled
GitHub Pages. CI has been green since.

### October–November 2026 — the pipeline wired end to end (feature 002)

The layers were tested in isolation, but the durable workflow's data steps were
placeholders and nothing wrote the graph. Feature 002
(`.specify/specs/002-pipeline-wiring/plan.md`) connected them:

- a configurable watchlist (8 equities and 5 FRED series by default);
- raw payloads that record their request parameters;
- FRED first-release dates and Tiingo daily price history, with response parsers;
- news-to-asset matching by ticker and company name;
- CORRELATES_WITH and INFLUENCES edges under a strict no-lookahead rule;
- plain step functions shared by the workflow and the CLI;
- `aetheris run`, `graph upsert`, `score`, `evaluate`, and `run --resume` working
  on real data;
- a byte-identical, lookahead-free replay test.

Hardening found along the way: graph timestamps stored in UTC on every machine,
rate limiting per quota window (hour and day, not just minute), a request-specific
4xx no longer disabling a provider for the run, a hermetic accessibility gate,
FRED requests windowed to stay under its vintage limit, failed fetches reported
in the run output, and news published after the run's as-of time excluded.

## Known gaps

These are tracked in the README's Known gaps section, which CI keeps honest:

- the scheduled pipeline scores with the deterministic stub model, not
  FinSense-ModernBERT;
- quotas are counted per process, not per calendar day;
- a failed fetch is not retried within the run;
- news comes only from Finnhub's general category;
- `aetheris ingest <provider>` may be served by another provider via fallback;
- `graph upsert --rebuild-edges` changes nothing, and `--since/--until` filter
  only sentiment metrics;
- graph files written on a non-UTC host before the UTC change keep local times;
- the CLI query grammar (`aetheris graph query`) differs from its contract
  (`aetheris query`);
- the `align()` helper is tested but the pipeline applies the as-of rule through
  the edge statistics instead.

## Planned milestones

Each of these needs its own spec clarification, plan, and task breakdown before
code, per Principle V. The order reflects dependencies, not commitments.

### M7 — Model-quality sentiment

- A `sentiment_model` setting passed through `run_sentiment`, with the ML extras
  documented, so scheduled runs use a manifest checkpoint instead of the stub.
- Chunk articles longer than a model's context window instead of rejecting them,
  and show the rejected count in `aetheris score`.
- Benchmark the primary model on a held-out financial news set before trusting
  it (its accuracy is so far only self-reported).

### M8 — Ingestion robustness and coverage

- Persist quota usage so daily limits hold across processes and runs.
- A bounded per-fetch retry policy for transient errors inside a run.
- Per-company Finnhub news and Tiingo news, raising match rates beyond the
  general feed.
- Forex, crypto, and index assets alongside equities.
- Resolve the CLI query grammar: add a top-level `aetheris query` or amend the
  contract.

### M9 — Operating at the planned scale

- Confirm or revise the scale assumption (~500 assets, ~25 series, ~3,000 news
  items a day); see [scale measurements](scale.md).
- Spread price ingestion across Tiingo's hourly quota and batch graph upserts.
- Bound correlation work, which grows with the square of the asset count.
- Measure SC-001 as a real P95 over many scheduled runs, and SC-002 and SC-003
  in operation.

### M10 — Operations

- Honour `--trigger` and wire scheduled and event triggers to a scheduler.
- Export metrics, and take MLflow's tracking URI from settings.
- Multi-process safety: avoid two processes running the same workflow, and stop
  a step's thread when its activity timeout fires.
- Optionally make the graph step transactional so readers never see a partial
  window.
- Rebuild or migrate graph files written before the UTC change.

### Governance decisions outstanding

- Ratify amendment A-001 (the accepted transitive licence deviations).
- Amend the constitution text that still names Neo4j and Redis as permitted.
- Remove the duplicate constitution file.
- Settle redistribution rights with Finnhub, FMP, and Tiingo before any derived
  data is shared, and add FRED's attribution string where data is shown.
- Resolve the spec's two entity-id formats (`equity:AAPL` in §3.3 versus
  `equity:NASDAQ:AAPL` in the data model, which the code follows).
