# Implementation Plan: Aetheris / NexusCap — Financial Intelligence Knowledge Graph & Sentiment Engine

**Branch**: `specs` | **Date**: 2026-10-10 | **Spec**: [`./spec.md`](./spec.md)

**Input**: Feature specification from `/specs/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command; its definition describes the execution workflow.

## Summary

Build a durable, modular Python pipeline that ingests macroeconomic series, asset prices, and
financial news from five free-tier providers; scores unstructured text with a permissively-licensed
financial sentiment model; and upserts the results into an embedded, typed property graph using an
idempotent, point-in-time-correct (backward as-of join, no lookahead) mutation path.

The technical approach is **embedded-library-first**: durable execution via DBOS Transact (MIT)
with a SQLite step journal, idempotent ingestion through a provider registry with an in-process
token bucket and TTL cache, sentiment via CPU-only transformer inference with temperature-scaling
calibration, and a relational-by-design graph store in DuckDB (MIT) exposed through a read-only
Python API plus a CLI. Observability uses the OpenTelemetry standard (spans/metrics/logs) exported
to a self-hosted Jaeger, with MLflow for model/run evaluation and Evidently for data drift.

No NEEDS CLARIFICATION items remain; every unknown was resolved in [`research.md`](./research.md).
Four constitution conflicts were identified during research and are recorded in
[Constitution Check](#constitution-check) and [Complexity Tracking](#complexity-tracking).

## Technical Context

**Language/Version**: Python 3.11+ (3.12 on Linux CI), CPU-only (no GPU)

**Primary Dependencies**:
- Orchestration: `dbos-transact` (MIT) — durable step journal in SQLite, retries with backoff, activity timeouts
- Graph: `duckdb` (MIT) — embedded store for nodes/edges, `INSERT … ON CONFLICT` idempotent upsert
- Raw payloads / journal / metrics: `sqlite3` (stdlib, public domain)
- Sentiment: `transformers` + `tokenizers` + `safetensors` + `torch` (all Apache-2.0 / BSD)
- Telemetry: `opentelemetry-sdk` (Apache-2.0) → OTLP; `prometheus_client` (Apache-2.0)
- Evaluation: `mlflow` (Apache-2.0), `evidently` (Apache-2.0)
- Testing: `pytest` (MIT), `vcrpy` (MIT) for recorded provider fixtures
- Container: Docker (for Jaeger + optional services only)

**Storage**: SQLite (public domain) for raw immutable payloads, the DBOS step journal, and metric
scrapes; DuckDB (MIT) for the knowledge graph. **No Redis/Valkey in v1** — see V2.

**Testing**: `pytest`; unit tests pure, integration tests replay `vcrpy` cassettes so no live
provider call ever consumes free-tier quota in CI (constitution "Testing Standards").

**Target Platform**: macOS (dev, Apple Silicon) and Linux x86_64 (CI); containerized for the
optional telemetry backends.

**Project Type**: Single project — embeddable Python library plus CLI. No network service exposed
(clarification Q4).

**Performance Goals**: SC-001 P95 end-to-end scheduled run < 5 min; SC-002 checkpoint resume < 60 s;
SC-003 cache hit ratio ≥ 80% at steady state.

**Constraints**: permissive-license-only runtime graph; free-tier quotas per §3.2.1 registry;
CPU-only model inference; zero commercial/SaaS dependency; all layers independently importable.

**Scale/Scope** *(ASSUMPTION — not in spec, requires confirmation)*: v1 tracks ~500 assets, ~25 FRED
series, and ≤3,000 news items/day, yielding an estimated 50k–250k `SentimentMetric` nodes and
~125k `CORRELATES_WITH` edges (500²/2 asset pairs). This is what sizes the DuckDB store and the
daily call budget; if the real scope is 10× larger, the ingestion schedule and edge-derivation
strategy must be revisited in `tasks.md`.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | Gate | Status |
|---|-----------|------|--------|
| I | Strict Open-Source Compliance | Every runtime dependency is MIT/Apache-2.0/BSD/public-domain; no paid tier, no SaaS | ⚠️ **CONDITIONAL** — 4 conflicts (V1–V4) |
| II | Decoupled Modular Architecture | Five layers are independently importable with no circular dependencies | ✅ PASS — enforced by `importlinter` contract |
| III | Resilient Durable Execution | Durable state machine, exact resume, bounded backoff, compensation on failure | ✅ PASS — DBOS journal + explicit compensating steps |
| IV | Comprehensive Observability & Evaluation | Spans on every workflow/fetch/inference/mutation; drift evaluated; OSS tracker | ✅ PASS — OTel→Jaeger, MLflow, Evidently |
| V | Specification-Driven Rigor | Every FR has a task and a failing test before code (spec §5.1) | ✅ PASS — FR-001…FR-021 register |

### Gate I conflicts (blocking, require explicit amendment — cannot be resolved inside `/plan`)

- **V1 — CRITICAL** — Constitution *Additional Constraints* names **Neo4j Community** as a permitted
  graph store, but Neo4j Community Edition is **GPLv3**, directly contradicting Principle I
  ("only MIT, Apache 2.0, BSD"). *Plan resolution:* Neo4j is not used; DuckDB (MIT) is used instead.
  The constitution line requires a documented amendment outside `/plan`.
- **V2 — CRITICAL** — Constitution names **Redis** as a permitted store for rate-limit counters, but
  Redis 8 is **AGPLv3**, again contradicting Principle I. *Plan resolution:* Redis is dropped from v1;
  the token bucket is in-process and thread-safe (§3.2 requires cross-thread, not cross-process,
  sharing), so no external counter store is needed at all. Valkey (BSD-3) is the compliant fallback
  if multi-process limiting is later required.
- **V3 — CRITICAL** — Spec §2/§3.3 cite **FinBERT** as an example sentiment model, but the
  `ProsusAI/finbert` and `yiyanghkust/finbert-tone` weight checkpoints declare **no license** on
  Hugging Face (i.e. all rights reserved), violating Principle I. *Plan resolution:* only
  Apache-2.0-licensed checkpoints are used (see `research.md` §D-1). The spec's "(e.g., FinBERT)"
  is illustrative, so no spec edit is mandatory, but the binding rule is recorded in
  `data-model.md` and `contracts/sentiment-schema.md`.
- **V4 — HIGH (business/legal, not license)** — Principle I *mandates* FRED, Alpha Vantage, Finnhub,
  FMP, and Tiingo, yet three of the five state in their current free-tier terms that data is for
  **personal/internal use only** and may not be redistributed (Finnhub "Personal Use"; FMP requires
  a separate Display & Licensing Agreement; Tiingo "internal and personal use only"). *Plan
  resolution:* v1 treats provider data as internal-only and does not redistribute raw provider data;
  the Aetheris graph (our own derived scores/edges) is the boundary. **Any redistribution of derived
  data needs written confirmation from FMP in particular.** This is an open product decision, not a
  code decision.

**Post-Phase-1 re-check**: I–V all PASS once V1–V3 are handled by the dependency substitutions above
and V4 is confined to the stated internal-use boundary. No principle was diluted, reinterpreted, or
silently ignored.

## Project Structure

### Documentation (this feature)

```text
.specify/specs/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   ├── graph-api.md             # Read-only Python API surface (FR-021)
│   ├── cli.md                   # CLI command schema (FR-021)
│   ├── sentiment-schema.md      # JSON payload contract (FR-010)
│   └── adapter-protocol.md      # Ingestion layer boundary (Principle II)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/
├── config/
│   ├── settings.py               # typed settings; secrets from env only (never committed)
│   └── providers.py              # loads §3.2.1 registry (quotas, fallback order) as data
├── orchestration/
│   ├── workflows.py              # registered durable workflows (FR-001)
│   ├── checkpoints.py            # step-level checkpoint/resume (FR-002)
│   ├── retry.py                  # bounded backoff policy (FR-003)
│   ├── compensation.py           # compensating actions per fallible step
│   └── triggers.py               # schedule/event trigger surface (FR-016)
├── ingestion/
│   ├── base.py                   # Adapter protocol, shared fetch/immutable-store path
│   ├── registry.py               # provider selection + 429/5xx fallback (FR-020)
│   ├── cache.py                  # SQLite-backed TTL cache (FR-007)
│   ├── ratelimit.py              # thread-safe in-process token bucket (FR-008)
│   ├── payloads.py               # raw immutable, schema-versioned payload store (FR-015)
│   ├── fred_client.py            # macro series (FR-004)
│   ├── market_client.py          # prices + fundamentals (FR-005)
│   └── news_client.py            # news text for sentiment (FR-006)
├── sentiment/
│   ├── analyzer.py               # model inference, CPU-only (FR-009)
│   ├── calibration.py            # temperature scaling for confidence
│   ├── schema.py                 # payload builders + validation (FR-010)
│   └── models.py                 # pinned model ids/hashes and license manifest
├── graph/
│   ├── schema.py                 # node/edge DDL, identity keys
│   ├── repository.py             # idempotent upsert + backward as-of join (FR-012/13/14)
│   ├── queries.py                # 1–2 hop traversals as SQL
│   ├── api.py                    # read-only Python API (FR-021)
│   └── align.py                  # point-in-time alignment helpers
├── observability/
│   ├── telemetry.py              # OTel tracer/meter/logger setup (FR-017)
│   ├── metrics.py                # span-derived metric definitions (FR-018)
│   └── evaluate.py               # MLflow run + Evidently drift (FR-019)
├── cli/
│   └── main.py                   # argparse/typer CLI entrypoint
└── __init__.py

tests/
├── contract/                     # API/CLI/schema contract tests
├── integration/                  # VCR-cassette replay; no live provider calls
├── unit/                         # pure unit tests
├── fixtures/cassettes/           # recorded provider interactions
└── fixtures/models/              # tiny stub model for CI inference tests
```

**Structure Decision**: Single project. The source tree mirrors the five spec layers exactly
(`orchestration`, `ingestion`, `sentiment`, `graph`, `observability`) plus `config` and `cli`, so
every directory maps 1:1 onto a spec §3 section and an FR register column — which is what makes the
spec §5.1/§5.2 traceability gate mechanically checkable. Dependency direction is strictly one-way
(`cli → orchestration → {ingestion, sentiment} → graph`, with `observability` cross-cutting via
injected tracer objects) and is enforced in CI by `importlinter` contracts, satisfying Principle II
without a plugin framework (YAGNI).

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| **V1** Constitution permits Neo4j Community (GPLv3) | Research proved GPLv3 conflicts with Principle I | NetworkX (in-memory, no persistence) and Kùzu (archived/frozen after Apple acquisition, no security patches) both fail the durability requirement |
| **V2** Constitution permits Redis (AGPLv3 in Redis 8) | Research proved AGPL conflicts with Principle I | Dropping external counters entirely — an in-process thread-safe token bucket satisfies §3.2 ("across worker threads") with no additional process or dependency |
| **V3** Spec names FinBERT; weights unlicensed | Principle I is absolute on model licensing | VADER (MIT) alone is too weak for the product; the two Apache-2.0 transformer checkpoints are the only compliant options |
| DBOS has no saga DSL | Compensation must be hand-written per workflow | Prefect 3 (Apache-2.0) has a transaction/rollback API but drifts toward needing a self-hosted server, violating the in-process constraint |
| DuckDB instead of a graph-native engine | No permissive, mature, embedded, graph-native engine is safe to depend on today | LadybugDB (MIT) is the only compliant candidate but is a ~1-year-old fork with a PyPI name collision against an AGPL project; deferred until a genuine multi-hop traversal requirement exists (YAGNI) |
| Open risks to carry into `tasks.md` | DBOS Python SQLite backend GA status must be verified at pin time; §Scale/Scope is an unconfirmed assumption | Fallback to Prefect 3 if DBOS Python/SQLite is not GA; scale assumption to be confirmed before Milestone 3 scheduling |
