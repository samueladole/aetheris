# Project Specification: Financial Intelligence Knowledge Graph & Sentiment Analysis Engine (Aetheris / NexusCap)

## 1. Project Vision & Core Principles
This project implements an automated, modular, and fault-tolerant financial intelligence pipeline. It ingests macroeconomic data, asset prices (stocks, crypto, forex), and financial news, performs advanced sentiment analysis, and structures these entities into a dynamic Knowledge Graph.

### 1.1 Non-Negotiable Constitutional Constraints
- **Strictly Open Source:** Zero reliance on paid third-party software, commercial orchestration platforms, or paid APIs. Only free API tiers and open-source licenses (MIT, Apache 2.0) are permitted.
- **Workflow Orchestration & Durability:** The execution lifecycle must be handled via durable workflows capable of automatic state recovery, retries, and failure compensation.
- **Comprehensive Observability & Evaluation:** End-to-end tracing, metric logging, and evaluation pipelines must track traditional data operations and future agentic interactions.
- **High Decoupling:** Component boundaries must remain strict so that ingestion, sentiment analysis, graph modeling, and orchestration layers can be reused independently.

---

## Clarifications

### Session 2026-10-10

- Q: Which join rule must the pipeline use when macro indicators, asset prices, and sentiment scores are combined into the knowledge graph at the Graph Integration step? (FR-014) → A: Backward as-of join — each point uses only the most recent value already known at that timestamp, strictly no lookahead.
- Q: What numeric range and derivation rule must edge weights and confidence scores follow for the `INFLUENCES`, `CORRELATES_WITH`, and `TARGETS` edges? (FR-012) → B: Weight ∈ [-1, 1] signed (Pearson r for `CORRELATES_WITH`, signed influence for `INFLUENCES`); confidence ∈ [0, 1] derived from statistical significance and observation count.
- Q: What measurable targets will define "done" for pipeline performance and data freshness, so success can be asserted in a test rather than described with adjectives like "fault-tolerant"? (FR-018) → A: P95 end-to-end latency under 5 minutes per scheduled run; workflow recovery from checkpoint under 60 seconds; cache hit ratio at least 80% at steady state.
- Q: How will other tools read the Knowledge Graph in v1 — through a query interface the pipeline exposes, or only by reading the storage engine's files directly? → A: Read-only in-process Python API plus a CLI for ad-hoc queries; no network service.
- Q: What unit does the orchestrator checkpoint and resume at — a whole workflow step, or finer-grained work inside a step — and what bounds govern a step's retries before it is declared failed? (FR-002, FR-003) → A: Step-level checkpointing with bounded backoff (base 1s, ×2, max 5 attempts, jitter); on exhaustion the step is marked failed and its compensating action runs.

---

## 2. High-Level Architecture
The system consists of five decoupled layers:
1. **Workflow Orchestrator:** Manages state machines, execution graphs, and error recovery.
2. **Ingestion Workers:** Modular fetchers for FRED, Alpha Vantage, Finnhub, Financial Modeling Prep, and Tiingo with shared rate-limiting.
3. **Sentiment Engine:** Isolated compute workers running open-source financial NLP models (e.g., FinBERT).
4. **Knowledge Graph Engine:** Graph storage and mutation pipeline managing typed nodes (`Asset`, `MacroIndicator`, `SentimentMetric`) and weighted edges (`INFLUENCES`, `CORRELATES_WITH`, `TARGETS`).
5. **Observability & Evaluation Subsystem:** Distributed tracing, metric collection, and evaluation runners.

---

## 3. Low-Level Component Specifications

### 3.1 Orchestration & State Management (`src/orchestration/`)
- Must support workflow registration, step checkpointing, and automatic resumption upon failure.
  - **Checkpoint granularity:** one checkpoint per workflow step. Resumption restarts from the
    first step lacking a successful checkpoint, so no work is re-executed once a step completes.
- Retries unreliable external network boundaries with bounded exponential backoff and activity
  timeouts, then marks the step failed and runs its compensating action:
  - `base_delay_ms=1000`, `multiplier=2`, `max_attempts=5`, jitter enabled, plus a per-step
    activity timeout. These are configuration values, not code constants.

### 3.2 Ingestion & Caching Subsystem (`src/ingestion/`)
- **Adapters:**
  - `fred_client.py`: Pulls U.S. macro series (GDP, CPI, Federal Funds Rate).
  - `market_client.py`: Pulls stock, forex, crypto prices, and fundamentals.
  - `news_client.py`: Pulls financial news articles and headlines, providing the unstructured
    text input consumed by the Sentiment Engine. Providers are selected from the registry in
    §3.2.1; Alpha Vantage's `NEWS_SENTIMENT` endpoint is excluded as a sentiment input source
    because pre-computed vendor sentiment would violate Open-Source Compliance (§I).
- **Caching & Rate Limiting:**
  - Local caching layer with configurable TTLs to prevent hitting strict free-tier quotas.
  - Shared token-bucket/sliding-window rate limiter across worker threads.

### 3.2.1 Provider Adapter Registry

All free-tier constraints below are configuration, not code constants; the shared rate limiter
must read them from this table so quota budgets are tunable without code changes.

| Provider | Module | Endpoints (v1) | Free-Tier Limit | Fallback Order |
|----------|--------|----------------|-----------------|----------------|
| FRED | `fred_client.py` | `series/observations` | 120 req/min | — |
| Alpha Vantage | `market_client.py` | `GLOBAL_QUOTE`, `FX_DAILY`, `CRYPTO_RATING` | 25 req/day | 3 |
| Finnhub | `market_client.py`, `news_client.py` | `quote`, `/news`, `stock/profile2` | 60 req/min | 4 |
| Financial Modeling Prep | `market_client.py` | `quote`, `historical-price-full`, `fmp/articles` | 250 req/day | 5 |
| Tiingo | `market_client.py` | `tiingo/daily`, `iex`, `tiingo/news` | 500 req/hr | 6 |

If a provider responds 429 or 5xx, the orchestrator routes the next request to the next
non-exhausted provider for the same data type; the failure is recorded as a trace span.

### 3.3 Sentiment Analysis Pipeline (`src/sentiment/`)
- **Analyzer:** `sentiment_analyzer.py` utilizing open-source transformer models.
- **Output Schema:** JSON payload containing `timestamp`, `entity_id`, `polarity_score` (-1.0 to 1.0), `confidence`, and `source_reference`.
  - `entity_id`: resolves to a node in the Knowledge Graph (§3.4); format is
    `<asset_class>:<symbol>` (e.g., `equity:AAPL`) or `macro:<indicator_code>`.
  - `source_reference`: stable identifier of the ingested news item (provider + article ID),
    enabling `SentimentMetric` nodes to link back to their source text.

### 3.4 Knowledge Graph Engine (`src/graph/`)
- **Schema:**
  - Nodes: `Asset` (ticker, name, asset_class), `MacroIndicator` (indicator_code, name, unit), `SentimentMetric` (score, timestamp).
  - Edges: `INFLUENCES`, `CORRELATES_WITH`, `TARGETS` with numerical weights and confidence scores.
    - **Weight** ∈ [-1.0, 1.0] (signed): `CORRELATES_WITH` carries the Pearson correlation coefficient, `INFLUENCES` carries a signed influence strength, `TARGETS` carries the strength of the sentiment-to-asset link.
    - **Confidence** ∈ [0.0, 1.0], derived from statistical significance and observation count (not model certainty).
- **Read Access (v1):** The Knowledge Graph is exposed through a read-only, in-process Python
  API plus a CLI for ad-hoc queries. No network service is exposed in v1; consumers import the
  library or invoke the CLI.
- **Mutation Logic:** Idempotent upsert logic to ensure data harmonization across disparate frequencies (high-frequency ticks vs. low-frequency macro data).
  - Alignment across disparate frequencies MUST use a **backward as-of join** with a strict **no-lookahead invariant**: a data point may only observe values already published at its own timestamp, guaranteeing point-in-time correctness.

### 3.5 Observability Subsystem (`src/observability/`)
- Captures distributed spans across ingestion requests, sentiment model inferences, and graph writes.
- Tracks metrics including system latency, error rates, cache hit/miss ratios, and queue depths.

---

## 4. Execution Workflow Sequence
1. **Trigger:** Pipeline schedule or event triggers the Workflow Orchestrator.
2. **Ingestion Step:** Fetch workers query external APIs (checking cache first) and store raw immutable payloads.
3. **Sentiment Step:** Unstructured text is processed through the sentiment pipeline, generating scored metrics.
4. **Graph Integration:** Numerical indicators and sentiment outputs are aligned via backward as-of join (strictly no lookahead) and upserted into the knowledge graph.
5. **Evaluation:** Telemetry and output consistency are validated and logged to the observability backend.

---

## 5. Implementation Roadmap Milestones
- **Milestone 1:** Repository structure setup & configuration of `.spec/` guidelines.
- **Milestone 2:** Orchestration core & state persistence layer.
- **Milestone 3:** Ingestion adapters and rate-limiting caching subsystem.
- **Milestone 4:** Sentiment analysis pipeline and open-source model integration.
- **Milestone 5:** Knowledge graph schema and mutations.

### 5.1 Specification-First Gate (Constitutional Precondition)

Before any code is written for a milestone, the following artifacts MUST exist for that
milestone's scope, in order:

1. `plan.md` — technical design, data model, and contracts for the milestone's FRs.
2. `tasks.md` — task list where every task maps to one or more FR IDs from §5.2.
3. A failing test for each task, per constitution §V (red-green-refactor).

A milestone may not be marked complete while its `tasks.md` contains unmapped tasks or while
any constitutional principle lacks test coverage. Re-run `/speckit.analyze` after `tasks.md`
is generated to validate cross-artifact consistency.

### 5.2 Requirement Register & Traceability Matrix

Every requirement carries a stable ID. `tasks.md` MUST reference these IDs.

| ID | Requirement (abbreviated) | Spec Ref | Milestone |
|----|---------------------------|----------|-----------|
| FR-001 | Register and invoke durable workflows | §3.1 | M2 |
| FR-002 | Checkpoint each workflow step and resume from the first failed step | §3.1 | M2 |
| FR-003 | Retry network boundaries with capped backoff (1s base, ×2, 5 attempts, jitter) | §3.1 | M2 |
| FR-004 | Ingest U.S. macro series (GDP, CPI, Fed Funds Rate) | §3.2 | M3 |
| FR-005 | Ingest asset prices and fundamentals (stock/forex/crypto) | §3.2 | M3 |
| FR-006 | Ingest financial news text for sentiment input | §3.2 | M3 |
| FR-007 | Serve cached raw payloads with configurable per-endpoint TTLs | §3.2 | M3 |
| FR-008 | Enforce shared token-bucket rate limit across worker threads | §3.2 | M3 |
| FR-009 | Score unstructured text with an open-source sentiment model | §3.3 | M4 |
| FR-010 | Emit sentiment JSON with the schema in §3.3 | §3.3 | M4 |
| FR-011 | Store Asset, MacroIndicator, SentimentMetric nodes | §3.4 | M5 |
| FR-012 | Store INFLUENCES/CORRELATES_WITH/TARGETS edges with signed weight [-1,1] and confidence [0,1] | §3.4 | M5 |
| FR-013 | Upsert mutations idempotently across disparate data frequencies | §3.4 | M5 |
| FR-014 | Align indicators and sentiment before upsert (backward as-of join, no lookahead) | §4 | M5 |
| FR-015 | Persist raw immutable payloads with schema version | §4 | M3 |
| FR-016 | Trigger pipeline by schedule or event | §4 | M2 |
| FR-017 | Emit trace spans for every workflow/fetch/inference/mutation | §3.5 | M6 |
| FR-018 | Collect latency, error-rate, cache hit/miss, queue-depth metrics | §3.5 | M6 |
| FR-019 | Run evaluation and log telemetry to the observability backend | §4 | M6 |
| FR-020 | Cover all five providers with free-tier quota safeguards | §3.2 | M3 |
| FR-021 | Expose read-only graph query API plus CLI for v1 consumers | §3.4 | M5 |

- **Milestone 6:** Telemetry observability, evaluation runners, and metric instrumentation
  (FR-017, FR-018, FR-019).

**Milestone closure rule**: a milestone closes only when every FR in its column has at
least one task and at least one passing test.

---

## 6. Success Criteria

### Measurable Outcomes

- **SC-001**: P95 end-to-end latency for a scheduled pipeline run is under 5 minutes.
- **SC-002**: A failed workflow resumes from its last checkpoint and completes in under 60 seconds.
- **SC-003**: Cache hit ratio is at least 80% at steady state.
- **SC-004**: Every FR ID in §5.2 has at least one passing test before its milestone may close.

Each outcome is computed from the metrics defined in §3.5 (latency, error rates, cache
hit/miss) and from checkpoint timestamps recorded by the orchestrator, so no additional
instrumentation is required to verify them.