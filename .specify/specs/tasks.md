# Tasks: Aetheris — Financial Intelligence Knowledge Graph & Sentiment Engine

**Input**: Design documents from `/specs/spec.md`, `/specs/plan.md`, `/specs/research.md`, `/specs/data-model.md`, `/specs/contracts/`, `/specs/quickstart.md`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included. The feature specification does not merely *permit* tests — constitution §V and
spec §5.1 make them mandatory: *"A failing test for each task, per constitution §V
(red-green-refactor)."* Every user-story phase therefore begins with test tasks written first and
confirmed failing before implementation begins.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: `src/`, `tests/` at repository root (per plan.md "Structure Decision")

---

## Story Derivation Note (read this first)

**`spec.md` contains no user stories.** It is organized by milestones M1–M6 and an FR register
(FR-001…FR-021). This was recorded as a HIGH-severity gap in the earlier `/speckit.analyze` pass
(issue S14: "No User Stories / Acceptance Scenarios") and remains unfixed.

Rather than invent new scope, the five stories below are **derived mechanically** from the spec's
own artifacts — each maps one-to-one onto a milestone and a contiguous block of FRs, so all
traceability required by spec §5.1/§5.2 is preserved:

| Story | Priority | Derived from | Milestone | FRs covered |
|-------|----------|--------------|-----------|-------------|
| US1 | P1 | Durable orchestration layer | M2 | FR-001, FR-002, FR-003, FR-016, FR-017 |
| US2 | P2 | Ingestion & caching subsystem | M3 | FR-004, FR-005, FR-006, FR-007, FR-008, FR-015, FR-020 |
| US3 | P3 | Sentiment analysis pipeline | M4 | FR-009, FR-010, FR-017 |
| US4 | P4 | Knowledge graph engine + read access | M5 | FR-011, FR-012, FR-013, FR-014, FR-021 |
| US5 | P5 | Observability & evaluation subsystem | M6 | FR-017, FR-018, FR-019 |

Sequencing follows the spec's own execution order (§4) and its milestone order (§5), so story order
here is the spec's order, not an independent prioritisation.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure — corresponds to Milestone 1.

- [X] T001 [P] Create the directory skeleton per plan.md "Project Structure": `src/{config,orchestration,ingestion,sentiment,graph,observability,cli}/__init__.py`, `tests/{unit,contract,integration}/`, `tests/fixtures/{cassettes,models}/`, and `.gitignore`
- [X] T002 [P] Create packaging and pinned dev-dependency manifest in `pyproject.toml` including `dbos-transact`, `duckdb`, `transformers`, `torch`, `safetensors`, `opentelemetry-sdk`, `prometheus-client`, `mlflow`, `evidently`, `pytest`, `vcrpy`, `ruff`, `mypy`, `import-linter`
- [X] T003 [P] Configure pytest in `pyproject.toml` `[tool.pytest.ini_options]` and CI in `.github/workflows/ci.yml` running `pytest`, `lint-imports`, and `aetheris check-licenses --strict`
- [X] T004 [P] Add an empty import-linter config in `.importlinter` at repo root (layer contracts are filled in by T013)

**Checkpoint**: Repository scaffold exists; `pytest --collect-only` runs cleanly.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T005 [P] Implement typed settings loader in `src/config/settings.py` reading values from environment variables only, with no secret ever written to a file (constitution "Secrets")
- [X] T006 [P] Implement the provider registry data loader in `src/config/providers.py` holding the spec §3.2.1 table (provider → module → endpoints → free-tier limit → fallback order) as data, not code
- [X] T007 [P] Implement OpenTelemetry tracer, meter, and logger initialisation in `src/observability/telemetry.py` with an OTLP exporter and a no-op fallback when no collector is reachable
- [X] T008 [P] Implement pipeline metric definitions in `src/observability/metrics.py`: latency histogram, fetch and error counters, cache hit/miss counters, and queue-depth gauge
- [X] T009 [P] Implement the immutable raw payload store in `src/ingestion/payloads.py` as append-only SQLite with `payload_id = "${provider}:${endpoint}:${request_hash}:${fetched_at}"` and a `schema_version` column (FR-015, VR-8)
- [X] T010 [P] Implement the shared TTL cache in `src/ingestion/cache.py` backed by SQLite with per-endpoint TTL values read from the registry (FR-007)
- [X] T011 [P] Implement the thread-safe in-process token bucket in `src/ingestion/ratelimit.py`, configured from registry limits and shared across worker threads (FR-008)
- [X] T012 [P] Implement the DuckDB node/edge DDL in `src/graph/schema.py` with primary keys from data-model.md §1–§2 and CHECK constraints enforcing VR-1 `SentimentMetric.score BETWEEN -1.0 AND 1.0`, VR-2 `confidence BETWEEN 0.0 AND 1.0`, VR-3 `Edge.weight BETWEEN -1.0 AND 1.0`, VR-4 `Edge.confidence BETWEEN 0.0 AND 1.0`, plus `asset_class` and `rel_type` enums
- [X] T013 [P] Define the import-linter layer contracts in `.importlinter` encoding every prohibition in `contracts/adapter-protocol.md` §Prohibited Dependencies (`ingestion` must not import `orchestration`, `sentiment`, or `graph`; `sentiment` must not import `graph`; `graph` must not import `ingestion`; `orchestration` may import all)
- [X] T014 Implement the CLI entrypoint skeleton in `src/cli/main.py` with the global flags `--config`, `--db`, `--raw`, `--json`, `--trace` and the exit codes `0` success, `1` runtime failure, `2` usage error, `3` license-gate failure
- [X] T015 [P] Implement the license manifest gate in `src/cli/licenses.py` enumerating the permitted set `{Apache-2.0, MIT, BSD-3-Clause, public-domain}` and rejecting anything else with exit code 3 (Principle I)

**Checkpoint**: Foundation ready — all user story implementation can now begin.

---

## Phase 3: User Story 1 — Durable orchestration & recovery (Priority: P1) 🎯 MVP

**Goal**: The pipeline executes as a durable workflow that checkpoints each step, resumes from the first failed step on restart, retries network calls with bounded exponential backoff, and runs a compensating action when a step is exhausted. This is the backbone every later story hangs off.

**Independent Test**: Inject a failing step, run `aetheris run --trigger schedule`, confirm it fails after bounded retries and enters compensation; then run `aetheris run --trigger schedule --resume <WORKFLOW_ID>` and confirm it resumes at the first failed step, skips completed steps, and finishes — with resume time under 60 s (SC-002).

### Tests for User Story 1 (write FIRST; ensure they FAIL before implementation) ⚠️

- [ ] T016 [P] [US1] Write failing unit test for the bounded exponential backoff policy in `tests/unit/test_retry.py` asserting `base_delay_ms=1000`, `multiplier=2`, `max_attempts=5`, jitter present, and that a 6th attempt is never made (FR-003)
- [ ] T017 [P] [US1] Write failing unit test for the compensating-action runner in `tests/unit/test_compensation.py` asserting the `FAILED → COMPENSATING → COMPENSATED` transition and that compensation failure is surfaced, not swallowed
- [ ] T018 [P] [US1] Write failing integration test for checkpoint resume in `tests/integration/test_checkpoint_resume.py` asserting resume starts at the first failed step and that completed steps are not re-executed (FR-002)

### Implementation for User Story 1

- [ ] T019 [P] [US1] Implement the bounded retry policy in `src/orchestration/retry.py` with `base_delay_ms`, `multiplier`, `max_attempts`, jitter, and a per-step activity timeout, all read from settings rather than hard-coded (FR-003)
- [ ] T020 [P] [US1] Implement the compensation registry and runner in `src/orchestration/compensation.py` mapping each fallible step name to its compensating action
- [ ] T021 [P] [US1] Implement step-level checkpointing in `src/orchestration/checkpoints.py` on the DBOS SQLite journal, with exactly one checkpoint per workflow step (FR-002)
- [ ] T022 [US1] Implement workflow registration and invocation in `src/orchestration/workflows.py` covering the five-step sequence from spec §4 (trigger → ingestion → sentiment → graph integration → evaluation) (FR-001)
- [ ] T023 [US1] Implement the trigger surface in `src/orchestration/triggers.py` accepting either a schedule or an event source (FR-016)
- [ ] T024 [US1] Wire `aetheris run [--trigger schedule|event] [--dry-run] [--resume WORKFLOW_ID]` into `src/cli/main.py` delegating to `src/orchestration/workflows.py`
- [ ] T025 [US1] Instrument workflow and step boundaries with OpenTelemetry spans in `src/orchestration/workflows.py`, emitting a span event for every retry, failure, and compensation (FR-017)
- [ ] T026 [US1] Run `pytest tests/unit/test_retry.py tests/unit/test_compensation.py tests/integration/test_checkpoint_resume.py` until green and record the measured resume latency against SC-002 (< 60 s) in `docs/scale.md`

---

## Phase 4: User Story 2 — Rate-limited ingestion with immutable payloads (Priority: P2)

**Goal**: All five providers are ingested behind a shared token bucket and TTL cache, storing raw immutable payloads, with automatic fallback when a provider is rate-limited or exhausted. This is the first story that produces real data.

**Independent Test**: Run `aetheris ingest fred series/observations` twice — the second call must be a cache hit (SC-003) with no provider request issued. Replay a poisoned 429 cassette and confirm the registry falls back to the next non-exhausted provider and records a span event (FR-020). Confirm re-running an ingest never mutates an existing raw payload row (VR-8).

### Tests for User Story 2 (write FIRST; ensure they FAIL before implementation) ⚠️

- [ ] T027 [P] [US2] Write failing contract test for the adapter protocol in `tests/contract/test_adapter_protocol.py` asserting invariants A1 (raw payloads only, no domain objects), A2 (no adapter imports another adapter), A3 (no retry logic in adapters), A5 (adapters importable in isolation), and A7 (typed `ProviderUnavailable` on 429/5xx)
- [ ] T028 [P] [US2] Write failing unit test for the TTL cache in `tests/unit/test_cache.py` asserting a hit on an identical second call, a miss after TTL expiry, and that `--force` bypasses the cache (FR-007)
- [ ] T029 [P] [US2] Write failing unit test for the token bucket in `tests/unit/test_ratelimit.py` asserting that concurrent threads sharing one limiter never exceed the configured rate (FR-008)
- [ ] T030 [P] [US2] Write failing unit test for the immutable payload store in `tests/unit/test_payloads.py` asserting append-only behaviour, presence of `schema_version`, and that a re-ingest with the same `payload_id` is a no-op rather than an overwrite (FR-015, VR-8)
- [ ] T031 [P] [US2] Write failing integration test for provider fallback in `tests/integration/test_provider_fallback.py` replaying a 429 cassette and asserting the next non-exhausted provider is used in fallback order and the failure is recorded as a span event (FR-020)

### Implementation for User Story 2

- [ ] T032 [US2] Implement the adapter base and `ProviderUnavailable` in `src/ingestion/base.py` per `contracts/adapter-protocol.md`, with a `fetch()` that returns raw payloads only (A1)
- [ ] T033 [US2] Implement provider selection and fallback in `src/ingestion/registry.py` driven entirely by the registry data from `src/config/providers.py`, ordered by the fallback-order column (FR-020)
- [ ] T034 [P] [US2] Implement the FRED macro adapter in `src/ingestion/fred_client.py` for GDP, CPI, and the Federal Funds Rate via `series/observations` (FR-004)
- [ ] T035 [P] [US2] Implement the market adapter in `src/ingestion/market_client.py` for stock, forex, and crypto prices plus fundamentals via `GLOBAL_QUOTE`, `FX_DAILY`, `CRYPTO_RATING`, `quote`, `historical-price-full`, and `tiingo/daily` (FR-005)
- [ ] T036 [P] [US2] Implement the news adapter in `src/ingestion/news_client.py` returning article title, body, and published timestamp for sentiment input, excluding Alpha Vantage `NEWS_SENTIMENT` per spec §3.2 (FR-006)
- [ ] T037 [US2] Wire the retry policy from `src/orchestration/retry.py` into the fetch path in `src/ingestion/base.py` so adapters contain no retry logic of their own (invariant A3)
- [ ] T038 [US2] Wire the cache-before-fetch check into `src/ingestion/registry.py` so a cached payload short-circuits the provider call and increments the cache-hit counter (FR-007)
- [ ] T039 [US2] Wire `aetheris ingest <provider> <endpoint> [--force] [--ttl SECONDS]` into `src/cli/main.py`, delegating to `src/ingestion/registry.py`
- [ ] T040 [US2] Instrument every fetch, cache decision, and fallback with spans and counters in `src/ingestion/registry.py` (FR-017, FR-018)
- [ ] T041 [P] [US2] Record `vcrpy` cassette fixtures for all five providers in `tests/fixtures/cassettes/` so no CI test issues a live provider call (invariant A6)
- [ ] T042 [US2] Run `pytest tests/contract/test_adapter_protocol.py tests/unit/test_cache.py tests/unit/test_ratelimit.py tests/unit/test_payloads.py tests/integration/test_provider_fallback.py` until green and record the steady-state cache hit ratio against SC-003 (≥ 80%) in `docs/scale.md`

**Checkpoint**: User Stories 1 AND 2 work independently — the pipeline durably ingests real provider data within quota, with immutable raw storage.

---

## Phase 5: User Story 3 — Sentiment scoring (Priority: P3)

**Goal**: Unstructured news text becomes calibrated `SentimentMetric` payloads that satisfy the wire contract and never emit an out-of-range or uncalibrated confidence value.

**Independent Test**: Run `aetheris score --since 2026-10-01T00:00:00+00:00 --json` and confirm every emitted payload conforms to `contracts/sentiment-schema.md`, with `polarity_score ∈ [-1, 1]`, `confidence ∈ [0, 1]` carrying a populated `calibration` block, and a permissive `model.license`. Then assert each of the seven rejection rules raises `ValidationError` rather than producing a partial payload.

### Tests for User Story 3 (write FIRST; ensure they FAIL before implementation) ⚠️

- [ ] T043 [P] [US3] Write failing contract test for the sentiment payload schema in `tests/contract/test_sentiment_schema.py` covering all seven rejection rules in `contracts/sentiment-schema.md` (out-of-range score, out-of-range or uncalibrated confidence, naive timestamp, text over 8192 chars, non-permissive model license, unresolvable `entity_id`, duplicate `metric_id` with identical `model.revision`)
- [ ] T044 [P] [US3] Write failing unit test for the polarity mapping in `tests/unit/test_analyzer.py` asserting `polarity = P(positive) − P(negative)` and `confidence = max(P(pos), P(neg), P(neu))` on CPU (FR-009, FR-010)
- [ ] T045 [P] [US3] Write failing unit test for temperature scaling in `tests/unit/test_calibration.py` asserting that calibrated confidence stays within [0.0, 1.0] and differs from the raw softmax maximum (VR-2)

### Implementation for User Story 3

- [ ] T046 [US3] Implement the pinned model manifest in `src/sentiment/models.py` recording `id`, `revision` hash, and `license` for the primary `AnkitAI/FinSense-ModernBERT-Financial-News-Sentiment-Analysis`, the fallback `mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis`, and the `vaderSentiment` baseline, rejecting any model whose license is not in `{Apache-2.0, MIT, BSD-3-Clause}` (V3 gate)
- [ ] T047 [US3] Implement CPU inference with the polarity/confidence mapping in `src/sentiment/analyzer.py`, using `torch.set_num_threads` and batched headlines, with no GPU requirement
- [ ] T048 [P] [US3] Implement the temperature-scaling calibration module in `src/sentiment/calibration.py`, fitted on a held-out Financial PhraseBank `sentences_allagree` split
- [ ] T049 [US3] Implement the payload builder and validator in `src/sentiment/schema.py` producing `schema_version` 1.0 payloads with the full `model` lineage block, `calibration` block, and `provenance` block per `contracts/sentiment-schema.md` (FR-010)
- [ ] T050 [P] [US3] Add a tiny deterministic stub model fixture in `tests/fixtures/models/` so CI inference runs without downloading multi-hundred-megabyte weights
- [ ] T051 [US3] Wire `aetheris score [--entity ENTITY_ID] [--since ISO8601] [--model MODEL_ID]` into `src/cli/main.py`, exiting with code 3 when `--model` is absent from `src/sentiment/models.py`
- [ ] T052 [US3] Instrument model inference with an OpenTelemetry span per scored item in `src/sentiment/analyzer.py` (FR-017)
- [ ] T053 [US3] Run `pytest tests/contract/test_sentiment_schema.py tests/unit/test_analyzer.py tests/unit/test_calibration.py` until green and confirm no uncalibrated confidence reaches the payload (VR-2)

**Checkpoint**: User Stories 1–3 work independently — raw news text becomes contract-valid, calibrated sentiment.

---

## Phase 6: User Story 4 — Point-in-time-correct graph upsert and read access (Priority: P4)

**Goal**: Aligned indicators and sentiment are upserted into the typed graph idempotently under the no-lookahead invariant, and exposed through a read-only API plus CLI. This is the story that delivers the actual knowledge graph.

**Independent Test**: Run `aetheris graph upsert --rebuild-edges` twice and confirm row counts are identical with no duplicate `(src_node, dst_node, rel_type, valid_from)` keys (VR-6). Run `tests/integration/test_point_in_time.py` and confirm a macro print released *after* a graph timestamp is not observable by that timestamp, and that a fixed cassette replays to a byte-identical graph state (VR-5, FR-014). Then run `aetheris query influencers --id equity:NASDAQ:AAPL --top 5 --json` and confirm contract-shaped output with tz-aware UTC timestamps.

### Tests for User Story 4 (write FIRST; ensure they FAIL before implementation) ⚠️

- [ ] T054 [P] [US4] Write failing point-in-time replay test in `tests/integration/test_point_in_time.py` asserting (a) a macro print released after a timestamp is not observable by that timestamp and (b) a fixed cassette produces a byte-identical graph state on replay (VR-5, FR-014)
- [ ] T055 [P] [US4] Write failing idempotency test in `tests/integration/test_upsert_idempotent.py` asserting repeated upserts create no duplicate `(src_node, dst_node, rel_type, valid_from)` keys and that re-scoring with a new `model.revision` creates a distinct row rather than overwriting (VR-6, FR-013)
- [ ] T056 [P] [US4] Write failing constraint test in `tests/unit/test_graph_schema.py` asserting the CHECK constraints reject `weight` outside [-1.0, 1.0] and `confidence` outside [0.0, 1.0] at the database layer, not only in application code (VR-3, VR-4)
- [ ] T057 [P] [US4] Write failing contract test for the read-only API in `tests/contract/test_graph_api.py` covering every function and error type in `contracts/graph-api.md`, including rejection of tz-naive datetimes with `InvalidWindowError` and `max_hops > 2` with `UnsupportedTraversalError` (FR-021)
- [ ] T058 [P] [US4] Write failing CLI contract test for the query grammar in `tests/contract/test_cli_query.py` covering `aetheris query entity|sentiment|edges|influencers|assets` and the `--json` output shape

### Implementation for User Story 4

- [ ] T059 [P] [US4] Implement node upsert in `src/graph/repository.py` for `Asset` (`asset_id = asset_class:exchange:symbol`), `MacroIndicator` (`indicator_code` provider-prefixed), and `SentimentMetric` (`metric_id = entity_id:source_reference:text_hash`) using the identity keys and uniqueness rules in data-model.md §1 (FR-011)
- [ ] T060 [P] [US4] Implement backward as-of alignment in `src/graph/align.py` storing both observation and availability timestamps and enforcing the strict no-lookahead invariant from verification rule VR-5 (FR-014)
- [ ] T061 [US4] Implement edge upsert in `src/graph/repository.py` for `INFLUENCES`, `CORRELATES_WITH` (canonical ordering `src_node < dst_node` to prevent duplicate pairs), and `TARGETS`, with signed `weight ∈ [-1.0, 1.0]`, `confidence ∈ [0.0, 1.0]`, `observation_count`, and the `valid_from`/`valid_to` window (FR-012)
- [ ] T062 [P] [US4] Implement the 1–2 hop query builders in `src/graph/queries.py` covering correlation, influencers, sentiment time windows, and asset listing with `min_confidence` applied before any limit
- [ ] T063 [US4] Implement the read-only API in `src/graph/api.py` exactly per `contracts/graph-api.md`, enforcing tz-aware UTC on all datetimes and capping traversal at 2 hops (FR-021)
- [ ] T064 [US4] Wire `aetheris graph upsert [--since] [--until] [--rebuild-edges]` and all `aetheris query` subcommands into `src/cli/main.py`, projecting the same dataclasses returned by `src/graph/api.py`
- [ ] T065 [US4] Emit an OpenTelemetry span for every graph write in `src/graph/repository.py`, including node upserts, edge upserts, and the alignment step (FR-017)
- [ ] T066 [US4] Run `pytest tests/integration/test_point_in_time.py tests/integration/test_upsert_idempotent.py tests/unit/test_graph_schema.py tests/contract/test_graph_api.py tests/contract/test_cli_query.py` until green and verify the SC-001 P95 upsert latency keeps a full scheduled run under 5 minutes

**Checkpoint**: All four stories work independently — the pipeline produces a durable, point-in-time-correct, queryable knowledge graph.

---

## Phase 7: User Story 5 — Observability & evaluation (Priority: P5)

**Goal**: Complete end-to-end telemetry, metric collection, and drift evaluation across every workflow, fetch, inference, and graph write, satisfying constitution §IV.

**Independent Test**: Run the full pipeline, then confirm Jaeger shows spans for every workflow, ingestion fetch, sentiment inference, and graph mutation boundary; confirm `aetheris evaluate` logs an MLflow run containing confidence and polarity distributions plus Evidently drift metrics; confirm `aetheris status` reports adapter state (`HEALTHY → RATE_LIMITED → EXHAUSTED → HEALTHY`) and cache hit ratio.

### Tests for User Story 5 (write FIRST; ensure they FAIL before implementation) ⚠️

- [ ] T067 [P] [US5] Write failing observability coverage test in `tests/integration/test_trace_coverage.py` asserting that at least one span is emitted for each of the four constitution §IV boundaries — workflow execution, ingestion fetch, sentiment inference, graph mutation (FR-017)
- [ ] T068 [P] [US5] Write failing unit test for metric definitions in `tests/unit/test_metrics.py` asserting the presence of latency, error-rate, cache hit/miss, and queue-depth series under their documented names (FR-018)

### Implementation for User Story 5

- [ ] T069 [P] [US5] Instrument the remaining ingestion, sentiment, and graph boundaries with spans and span events in `src/ingestion/registry.py`, `src/sentiment/analyzer.py`, and `src/graph/repository.py`, closing any gap found by `tests/integration/test_trace_coverage.py` (FR-017)
- [ ] T070 [P] [US5] Wire metric recording into `src/observability/metrics.py` and expose a scrape surface reachable from `src/cli/main.py`, recording the metrics that back SC-001, SC-002, and SC-003 (FR-018)
- [ ] T071 [US5] Implement MLflow run logging in `src/observability/evaluate.py` recording model outputs, confidence distributions, polarity distributions, and `model_id` lineage per run (FR-019)
- [ ] T072 [US5] Implement Evidently drift computation in `src/observability/evaluate.py` writing drift results to MLflow, to a Prometheus gauge, and to an OTel span event (FR-019)
- [ ] T073 [US5] Wire `aetheris evaluate [--since] [--min-confidence]` and `aetheris status [--verbose]` into `src/cli/main.py`, with `status` surfacing provider adapter state and quota consumption
- [ ] T074 [US5] Run `pytest tests/integration/test_trace_coverage.py tests/unit/test_metrics.py` until green and confirm SC-003 cache hit ratio and SC-001 P95 latency are both measurable from the emitted telemetry in `src/observability/metrics.py`

**Checkpoint**: All five user stories are independently functional, and constitution §IV is fully satisfied.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories, plus the verification gates the constitution requires.

- [ ] T075 [P] Write `README.md` documenting setup, dry-run usage, and the internal-use-only boundary for provider data from plan.md issue V4
- [ ] T076 [P] Run the full `.specify/specs/quickstart.md` validation end-to-end and fix every step that fails, including `aetheris check-licenses --strict`, `lint-imports`, and the STEP 7 point-in-time replay
- [ ] T077 [P] Confirm every FR ID in spec §5.2 (FR-001 … FR-021) has at least one passing test and record per-FR coverage in `docs/coverage.md` (SC-004)
- [ ] T078 [P] Measure a volume-scale run at the plan.md assumed scale (~500 assets, ~3k news items/day) and record actual timing and edge counts in `docs/scale.md`, confirming or revising the scale assumption
- [ ] T079 [P] Verify the DBOS Python SQLite backend is GA at the pinned version; if not GA, execute the documented Prefect 3 (Apache-2.0) fallback in `src/orchestration/workflows.py` and record the switch in `research.md` §A
- [ ] T080 Add a CI job to `.github/workflows/ci.yml` running `aetheris check-licenses --strict` and `lint-imports` on every pull request so Principle I and Principle II cannot regress silently
- [ ] T081 Re-run `/speckit.analyze` against `.specify/specs/spec.md`, `.specify/specs/plan.md`, and `.specify/specs/tasks.md` to confirm every task maps to an FR and that no unmapped tasks remain

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately. All four tasks are `[P]`.
- **Foundational (Phase 2)**: Depends on Setup completion — **BLOCKS all user stories**. Tasks T005–T013 and T015 are `[P]`; T014 (CLI skeleton) depends on T005 (settings) and T007 (telemetry).
- **User Stories (Phases 3–7)**: All depend on Foundational completion.
  - **US1 (Phase 3, M2)** must complete first: every other story writes through the orchestrator's workflow and trigger surface.
  - **US2 (Phase 4, M3)**, **US3 (Phase 5, M4)**, and **US5 (Phase 7, M6)** then depend only on US1 and can proceed in parallel.
  - **US4 (Phase 6, M5)** depends on US1, US2, and US3 — it consumes raw payloads, sentiment payloads, and orchestrated writes.
  - Stories may otherwise be worked on in parallel if staffed.
- **Polish (Phase 8)**: Depends on all desired user stories being complete.

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational — no dependencies on other stories.
- **User Story 2 (P2)**: Depends on US1 (runs inside an orchestrated workflow; retry policy comes from `src/orchestration/retry.py`).
- **User Story 3 (P3)**: Depends on US1; consumes news text produced by US2's adapter, but is testable standalone against the stub fixture from T050.
- **User Story 4 (P4)**: Depends on US1, US2, and US3 — it aligns numerical indicators and sentiment output before upsert (spec §4 step 4).
- **User Story 5 (P5)**: Depends on US1 for the workflow span boundary; the metric and drift layers are otherwise independent and can be built in parallel with US2–US4.

### Within Each User Story

- Tests MUST be written first and confirmed FAILING before implementation (constitution §V, spec §5.1).
- Schema/contract modules before consumers: `base.py` → `registry.py` → individual adapters; `schema.py`/`models.py` → `analyzer.py` → CLI wiring.
- Point-in-time alignment (`align.py`) before edge upsert, since edge upsert depends on aligned inputs.
- Instrumentation after the functional path works, so spans wrap real behaviour rather than placeholders.
- Story complete before moving to the next priority.

### Parallel Opportunities

- All four Phase 1 Setup tasks are `[P]` — T001–T004 can run concurrently.
- In Phase 2, T005–T013 and T015 are `[P]`; only T014 must wait for T005 and T007.
- In US1, the three test tasks (T016–T018) are `[P]`, as are the three implementation primitives T019 (retry), T020 (compensation), and T021 (checkpoints) before they are assembled in T022.
- In US2, the five test tasks (T027–T031) are `[P]`, and the three adapters T034, T035, T036 are `[P]` once `base.py` (T032) exists. Cassette recording (T041) is `[P]`.
- In US3, the three test tasks (T043–T045) are `[P]`, as are T048 (calibration) and T050 (stub model fixture).
- In US4, the five test tasks (T054–T058) are `[P]`, as are T059 (node upsert), T060 (alignment), and T062 (queries) before T061 consumes them.
- In US5, T067–T070 are `[P]`; only T071 must precede T072 in `evaluate.py`.
- **Cross-story parallelism once US1 lands**: US2, US3, and US5 can be built by three developers simultaneously, since US3's dependency on US2's news text is satisfied in tests by the stub fixture.

---

## Parallel Example: User Story 1

```bash
# Launch all tests for User Story 1 together (write first, confirm they fail):
Task: "Write failing unit test for the bounded exponential backoff policy in tests/unit/test_retry.py"
Task: "Write failing unit test for the compensating-action runner in tests/unit/test_compensation.py"
Task: "Write failing integration test for checkpoint resume in tests/integration/test_checkpoint_resume.py"

# Then the three orchestration primitives together:
Task: "Implement the bounded retry policy in src/orchestration/retry.py"
Task: "Implement the compensation registry and runner in src/orchestration/compensation.py"
Task: "Implement step-level checkpointing in src/orchestration/checkpoints.py"
```

## Parallel Example: User Story 2

```bash
# Once src/ingestion/base.py exists, all three adapters in parallel:
Task: "Implement the FRED macro adapter in src/ingestion/fred_client.py"
Task: "Implement the market adapter in src/ingestion/market_client.py"
Task: "Implement the news adapter in src/ingestion/news_client.py"

# Casette recording is independent of all adapter code:
Task: "Record vcrpy cassette fixtures for all five providers in tests/fixtures/cassettes/"
```

## Parallel Example: User Story 4

```bash
# The critical correctness paths, in parallel:
Task: "Write failing point-in-time replay test in tests/integration/test_point_in_time.py"
Task: "Write failing idempotency test in tests/integration/test_upsert_idempotent.py"
Task: "Implement backward as-of alignment in src/graph/align.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (**CRITICAL — blocks all user stories**)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: Test User Story 1 independently — `aetheris run --trigger schedule` fails a step, compensates, and resumes under 60 s
5. The durable execution backbone is now proven and can be shipped as the MVP increment

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → **durable execution backbone (MVP)**
3. Add User Story 2 → Test independently → real provider data flowing within free-tier quotas
4. Add User Story 3 → Test independently → calibrated sentiment payloads
5. Add User Story 4 → Test independently → **the knowledge graph itself, point-in-time-correct and queryable**
6. Add User Story 5 → Test independently → full telemetry, metrics, and drift evaluation (constitution §IV)
7. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. The team completes Setup + Foundational together
2. Once US1 lands, three streams proceed in parallel:
   - Developer A: User Story 2 (ingestion adapters and caching)
   - Developer B: User Story 3 (sentiment model and calibration)
   - Developer C: User Story 5 (telemetry, metrics, drift)
3. User Story 4 integrates all three into the graph
4. Polish closes out with the quickstart validation, coverage audit, and CI gates

---

## Notes

- `[P]` tasks = different files, no dependencies on incomplete tasks
- `[Story]` label maps task to a specific user story for traceability
- Each user story is independently completable and testable
- Tests must fail before implementing — verify at every story's first task
- **Constitution amendment still required**: plan.md records four conflicts (V1 Neo4j GPLv3, V2 Redis AGPL, V3 unlicensed FinBERT weights, V4 free-tier redistribution terms). V1–V3 are handled by dependency choices inside this task list, but the constitution itself still names Neo4j and Redis as permitted stores. Amendment must happen outside `/speckit.tasks`.
- The scale assumption (~500 assets, ~3k news items/day) is unconfirmed and is verified by T078, not assumed by it
- Commit after each task or logical group
- Stop at any checkpoint to validate the story independently
- Avoid: vague tasks, same-file conflicts, cross-story dependencies that break independence

---

