# Aetheris / NexusCap

**Financial Intelligence Knowledge Graph & Sentiment Analysis Engine**

Aetheris is an automated, modular, fault-tolerant pipeline that ingests macroeconomic data,
asset prices, and financial news; scores unstructured text with open-source financial NLP
models; and structures everything into a dynamic, queryable Knowledge Graph — all under a
strict open-source-only constraint.

> ### ⚠️ Current status: design only
>
> This repository currently contains the **specification and design package** — no Python
> source code exists yet. There is no runnable pipeline, no CLI, and no tests.
>
> The full specification-driven workflow chain has been completed and is committed:
> `spec → clarify → plan → tasks`. All 81 implementation tasks are described in
> [`.specify/specs/tasks.md`](.specify/specs/tasks.md) and are ready to execute.
>
> Everything below marked **[planned]** describes intended behaviour and will become
> accurate as the tasks land. Nothing in the "Usage" sections works today.

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [The five decoupled layers](#the-five-decoupled-layers)
- [Technology stack](#technology-stack)
- [Repository layout](#repository-layout)
- [Design documents](#design-documents)
- [Configuration](#configuration) **[planned]**
- [Usage](#usage) **[planned]**
- [Data model](#data-model)
- [Point-in-time correctness](#point-in-time-correctness)
- [Performance targets](#performance-targets)
- [Constitution](#constitution)
- [Provider terms & legal boundaries](#provider-terms--legal-boundaries)
- [Development](#development)
- [Roadmap](#roadmap)
- [Open decisions](#open-decisions)

---

## What it does

1. **Ingest** U.S. macro series, asset prices, and financial news from five free-tier providers,
   respecting per-endpoint quotas and caching aggressively.
2. **Score** unstructured news text into calibrated sentiment metrics with an open-source
   transformer model.
3. **Align** numerical indicators and sentiment into a single point-in-time-correct timeline.
4. **Upsert** everything idempotently into a typed Knowledge Graph with weighted, typed edges.
5. **Observe and evaluate** — distributed traces, metrics, and data-drift evaluation end to end.

The whole pipeline runs as a **durable workflow**: if any step fails, execution resumes from the
last completed step with bounded exponential backoff, and failed steps run compensating actions.

---

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │        Workflow Orchestrator            │
                    │  registration · checkpointing · resume  │
                    │  bounded backoff · compensation         │
                    └───────────────┬─────────────────────────┘
                                    │ drives
        ┌───────────────────────────┼───────────────────────────┐
        ▼                           ▼                           ▼
┌───────────────┐          ┌─────────────────┐        ┌──────────────────┐
│   Ingestion   │          │    Sentiment    │        │      Graph       │
│   Workers     │          │     Engine      │        │     Engine       │
│               │          │                 │        │                  │
│ fred_client   │──text───▶│  analyzer       │        │  node upsert     │
│ market_client │          │  calibration    │──score▶│  edge upsert     │
│ news_client   │          │  schema         │        │  as-of align     │
│               │          │                 │        │                  │
│ payload store │          └─────────────────┘        │  queries         │
│ ttl cache     │                                     │  read-only API   │
│ token bucket  │                                     └────────┬─────────┘
│ registry      │                                              │
└───────┬───────┘                                              ▼
        │                                            ┌──────────────────┐
        │         raw immutable payloads             │  Read-only API   │
        └────────────────────────────────────────────│  + CLI           │
                                                     └──────────────────┘
        ┌──────────────────────────────────────────────────────────┐
        │         Observability & Evaluation (cross-cutting)       │
        │   OTel spans/metrics/logs → Jaeger · MLflow · Evidently  │
        └──────────────────────────────────────────────────────────┘
```

Every arrow is a **hard boundary enforced in CI** by `import-linter` contracts: `ingestion`,
`sentiment`, and `graph` never import `orchestration`; `sentiment` never imports `graph`; `graph`
never imports `ingestion`. Tracers are injected rather than imported at module scope, so any layer
can be imported and tested in isolation.

See [`plan.md` → Project Structure](.specify/specs/plan.md#project-structure) for the exact tree.

---

## The five decoupled layers

| # | Layer | Path | Responsibility |
|---|-------|------|----------------|
| 1 | Workflow Orchestrator | `src/orchestration/` | Durable state machines, checkpoint/resume, bounded backoff, failure compensation |
| 2 | Ingestion Workers | `src/ingestion/` | Modular provider adapters, shared token-bucket rate limiting, TTL caching, immutable raw payloads |
| 3 | Sentiment Engine | `src/sentiment/` | Isolated CPU inference workers producing calibrated polarity scores |
| 4 | Knowledge Graph Engine | `src/graph/` | Typed nodes and weighted edges, idempotent upserts, point-in-time alignment, read-only queries |
| 5 | Observability & Evaluation | `src/observability/` | Distributed spans, metric collection, drift evaluation |

Each layer is independently importable and replaceable, so they can be reused in future
autonomous agentic workflows or standalone projects.

---

## Technology stack

Every runtime dependency is permissively licensed (constitution Principle I). Licenses below were
verified during Phase 0 research — see [`research.md`](.specify/specs/research.md).

| Concern | Choice | License |
|---|---|---|
| Durable orchestration | **DBOS Transact** — SQLite step journal, bounded retries, activity timeouts | MIT |
| Graph storage | **DuckDB** — embedded, typed node/edge tables, `ON CONFLICT` upsert | MIT |
| Raw payloads / journal | **SQLite** — append-only immutable store | public domain |
| Sentiment model | **FinSense-ModernBERT** (primary) · **distilroberta-finetuned-financial** (fallback) · **VADER** (baseline) | Apache-2.0 / Apache-2.0 / MIT |
| Model runtime | `transformers`, `tokenizers`, `safetensors`, `torch` (CPU only, no GPU) | Apache-2.0 / BSD-style |
| Telemetry | **OpenTelemetry** Python SDK + OTLP → **Jaeger** | Apache-2.0 |
| Metrics | `prometheus_client` → Prometheus | Apache-2.0 |
| Evaluation | **MLflow** (tracking, evals, model registry) | Apache-2.0 |
| Data drift | **Evidently** | Apache-2.0 |
| Testing | `pytest`, `vcrpy` (recorded cassettes — no live provider calls in CI) | MIT |
| Tooling | `ruff`, `mypy`, `import-linter` | permissive |

### Notable exclusions

These are deliberately **not** used, because their licenses violate Principle I:

| Excluded | Why |
|---|---|
| Neo4j Community Edition | GPLv3 — copyleft |
| Redis (8.x) | AGPLv3 |
| Grafana / Grafana Tempo | AGPL-3.0 |
| Uptrace | AGPL-3.0 |
| ClearML server | SSPL 1.0 (bundles MongoDB SSPL) |
| FalkorDB | SSPLv1 |
| Memgraph | BSL 1.1 (not an open-source license) |
| Restate | BSL 1.1 |
| TypeDB CE | MPL-2.0 |
| Kùzu | Frozen — repo archived after Apple acquisition; no security patches |
| ProsusAI/finbert, yiyanghkust/finbert-tone | **No license declared for the model weights** ("all rights reserved") |
| Loughran–McDonald lexicon | Academic use only; commercial license required |

---

## Repository layout

```
.
├── .gitignore
├── README.md                          ← you are here
├── .github/workflows/ci.yml           [planned]  pytest · lint-imports · license gate
├── src/                               [planned]
│   ├── config/                        # settings, provider registry data
│   ├── orchestration/                 # workflows, checkpoints, retry, compensation, triggers
│   ├── ingestion/                     # base, registry, cache, ratelimit, payloads, 3 adapters
│   ├── sentiment/                     # analyzer, calibration, schema, model manifest
│   ├── graph/                         # schema, repository, align, queries, api
│   ├── observability/                 # telemetry, metrics, evaluate
│   └── cli/                           # argparse entrypoint
├── tests/                             [planned]
│   ├── unit/ · contract/ · integration/
│   └── fixtures/cassettes/ · fixtures/models/
└── .specify/
    ├── memory/constitution.md         # project principles (authoritative)
    ├── specs/
    │   ├── spec.md                    # FR-001…FR-021, SC-001…SC-004
    │   ├── plan.md                    # architecture, tech context, constitution check
    │   ├── research.md                # Phase 0 decisions and license verification
    │   ├── data-model.md              # entities, identity, validation VR-1…VR-9
    │   ├── tasks.md                   # 81 tasks, story-organized
    │   ├── quickstart.md              # end-to-end validation guide
    │   └── contracts/                 # graph-api · cli · sentiment-schema · adapter-protocol
    ├── scripts/ · templates/ · workflows/
    └── integrations/
```

---

## Design documents

The artifacts are ordered as a chain — each one is the input to the next.

| Document | Purpose |
|---|---|
| [`spec.md`](.specify/specs/spec.md) | Requirements FR-001…FR-021 with milestone mapping, success criteria, provider adapter registry |
| [`plan.md`](.specify/specs/plan.md) | Technical context, constitution check with gate evaluation, project structure, complexity tracking |
| [`research.md`](.specify/specs/research.md) | License-verified decisions for orchestration, storage, telemetry, and models |
| [`data-model.md`](.specify/specs/data-model.md) | Node/edge schema, identity keys, state machines, validation rules VR-1…VR-9 |
| [`contracts/`](.specify/specs/contracts/) | The four public and internal interfaces |
| [`quickstart.md`](.specify/specs/quickstart.md) | Runnable validation scenarios proving the pipeline works end to end |
| [`tasks.md`](.specify/specs/tasks.md) | 81 tasks in 8 phases, every one traceable to an FR |
| [`constitution.md`](.specify/memory/constitution.md) | The five non-negotiable principles |

---

## Configuration

**[planned]** — configuration is driven by T005 (`src/config/settings.py`) and T006
(`src/config/providers.py`).

All settings come from environment variables or a TOML file. **API keys are never committed** —
they are supplied only via environment variables or git-ignored local files, per the constitution.

```bash
# Provider keys (only FRED is required for the dry run)
export FRED_API_KEY="..."          # required for macro series
export ALPHA_VANTAGE_API_KEY="..."
export FINNHUB_API_KEY="..."
export FMP_API_KEY="..."
export TIINGO_API_KEY="..."

# Storage locations
export AETHERIS_DB="./aetheris.duckdb"
export AETHERIS_RAW_DB="./aetheris-raw.db"

# Telemetry backends (omit to run fully offline)
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"
export MLFLOW_TRACKING_URI="sqlite:///mlflow.db"
```

Free-tier quotas for all five providers are held as **data** in the provider registry, not as code
constants, so budgets can be tuned without a code change:

| Provider | Free-tier limit | Fallback order | Redistribution |
|---|---|---|---|
| FRED | 120 req/min, no daily cap | — | ⚠️ third-party series restricted + attribution required |
| Alpha Vantage | 25 req/day | 3 | Least restrictive, but quota is tight |
| Finnhub | 60 req/min | 4 | ❌ "Personal Use" only |
| Financial Modeling Prep | 250 calls/day | 5 | ❌ requires Display & Licensing Agreement |
| Tiingo | 50 req/hour, 1000/day | 6 | ❌ internal use only |

---

## Usage

**[planned]** — none of this works yet. It is the intended surface, specified in
[`contracts/cli.md`](.specify/specs/contracts/cli.md) and
[`contracts/graph-api.md`](.specify/specs/contracts/graph-api.md), and delivered by tasks
T024, T039, T051, T064, and T073.

### CLI

```bash
# Execute the full pipeline durably
aetheris run --trigger schedule --dry-run
aetheris run --trigger schedule --resume <WORKFLOW_ID>

# Ingest a single provider
aetheris ingest fred series/observations
aetheris ingest finhub /news --force

# Score news text
aetheris score --since 2026-10-01T00:00:00+00:00 --json

# Upsert into the graph
aetheris graph upsert --rebuild-edges

# Query the graph (read-only)
aetheris query influencers --id equity:NASDAQ:AAPL --top 5 --json
aetheris query sentiment --entity equity:NASDAQ:AAPL --start 2026-10-01T00:00:00+00:00

# Evaluate and monitor
aetheris evaluate --since 2026-09-01T00:00:00+00:00
aetheris status

# Constitution Principle I gate
aetheris check-licenses --strict
```

Exit codes: `0` success · `1` runtime failure · `2` usage error · `3` license-gate failure.

### Python API

```python
from src.graph.api import get_entity, sentiment_for, top_influencers

# Point-in-time sentiment for one asset
points = sentiment_for(
    "equity:NASDAQ:AAPL",
    start=datetime(2026, 10, 1, tzinfo=timezone.utc),
    end=datetime(2026, 10, 15, tzinfo=timezone.utc),
    min_confidence=0.7,
)

# Strongest macro influences on an asset
for edge in top_influencers("equity:NASDAQ:AAPL", limit=5):
    print(edge.src_node, edge.weight, edge.confidence)
```

The API is **read-only and in-process** — no network service, no auth surface. Traversal is capped
at 2 hops in v1; datetimes must be timezone-aware UTC or the call raises `InvalidWindowError`.

---

## Data model

Three node types and three weighted edge types.

**Nodes**

| Node | Identity key | Key attributes |
|---|---|---|
| `Asset` | `asset_id` = `asset_class:exchange:symbol` | `ticker`, `name`, `asset_class` ∈ {equity, forex, crypto, index}, `exchange`, `currency` |
| `MacroIndicator` | `indicator_code` (provider-prefixed, e.g. `FRED:GDP`) | `name`, `unit`, `frequency`, `seasonal_adjustment` |
| `SentimentMetric` | `metric_id` = `entity_id:source_reference:text_hash` | `score` ∈ [-1, 1], `confidence` ∈ [0, 1], `timestamp`, `model_id` |

**Edges** — all share weight `∈ [-1.0, 1.0]` (signed), confidence `∈ [0.0, 1.0]`, an observation
window, and `observation_count`:

| Edge | Direction | Weight meaning |
|---|---|---|
| `INFLUENCES` | `MacroIndicator` → `Asset` | Signed influence strength (negative is meaningful) |
| `CORRELATES_WITH` | asset ↔ asset | Pearson correlation coefficient `r` |
| `TARGETS` | `SentimentMetric` → `Asset` | Strength of the sentiment-to-entity link |

Re-scoring the same text with a different model creates a **new** row rather than overwriting,
preserving lineage. Full schema, identity rules, state transitions, and validation rules VR-1…VR-9
are in [`data-model.md`](.specify/specs/data-model.md).

---

## Point-in-time correctness

The single most important correctness rule in this system:

> All alignment across disparate frequencies uses a **backward as-of join** with a strict
> **no-lookahead invariant** — a data point may only observe values already published at its own
> timestamp.

A CPI print released on the 12th covering January is *not* observable by a graph state on January
31. Conflating **observation time** with **availability time** is the classic way a financial
pipeline leaks future information and silently invalidates every downstream query.

This is enforced by VR-5 and verified deterministically by
[`tests/integration/test_point_in_time.py` (T054)](.specify/specs/tasks.md) — a fixed cassette must
replay to a byte-identical graph state. **Treat a failure here as a release blocker.**

---

## Performance targets

Measurable, and computed from metrics the system already emits — no extra instrumentation required.

| ID | Target |
|---|---|
| SC-001 | P95 end-to-end latency for a scheduled run < **5 minutes** |
| SC-002 | Failed workflow resumes from checkpoint and completes in < **60 seconds** |
| SC-003 | Cache hit ratio ≥ **80%** at steady state |
| SC-004 | Every FR-001…FR-021 has ≥ 1 passing test before its milestone closes |

Scale assumption (unconfirmed, verified by T078): ~500 assets, ~25 macro series, ~3,000 news
items/day → 50k–250k `SentimentMetric` nodes and ~125k `CORRELATES_WITH` edges.

---

## Constitution

Five non-negotiable principles govern every change. Full text:
[`.specify/memory/constitution.md`](.specify/memory/constitution.md).

1. **Strict Open-Source Compliance** — MIT, Apache 2.0, or BSD only. No paid tiers, no SaaS.
2. **Decoupled Modular Architecture** — every layer independently importable, no circular dependencies.
3. **Resilient Durable Execution** — checkpointing, exponential backoff, failure compensation, resilient network boundaries.
4. **Comprehensive Observability & Evaluation** — spans on every operation; outputs, confidence, and drift continuously evaluated.
5. **Specification-Driven Rigor** — no code without a spec update and task breakdown; red-green-refactor.

**Every PR must pass** `aetheris check-licenses --strict` (Principle I) and `lint-imports`
(Principle II). If you add a dependency, add it to the license manifest in
`src/cli/licenses.py` and pin its version.

---

## Provider terms & legal boundaries

**Aetheris consumes provider data internally and exports only its own derived artifacts**
(sentiment scores, weights, edges). Three of the five mandated providers state in their current
free-tier terms that data may not be redistributed:

- **Finnhub** — free tier is labelled "Personal Use"
- **Financial Modeling Prep** — redistribution requires a separate Display & Licensing Agreement
- **Tiingo** — "internal and personal use only… you may not redistribute the data in any form"

**Do not ship a public redistribution of derived data without written confirmation from these
providers.** This is recorded as issue **V4** in [`plan.md`](.specify/specs/plan.md) and is a
product decision, not an engineering one.

FRED additionally requires a visible attribution string and restricts redistribution of
third-party-copyrighted series.

---

## Development

The workflow is **specification-first**: no code is written without a corresponding spec update
and task breakdown (constitution Principle V).

```
spec.md ──▶ clarify ──▶ plan.md ──▶ tasks.md ──▶ implement
              │                       │
        resolve ambiguity      task ↔ FR traceability
```

**Adding a feature:**

1. Update `.specify/specs/spec.md` with a new FR ID (register lives in §5.2)
2. Run the Spec Kit commands to regenerate `plan.md` and `tasks.md`
3. Write the failing test first, then implement (red-green-refactor)
4. Confirm `lint-imports` and the license gate pass before marking a task complete

**Commands** (available via the OpenCode Spec Kit integration):

| Command | Purpose |
|---|---|
| `/speckit.analyze` | Cross-artifact consistency and coverage check |
| `/speckit.clarify` | Resolve ambiguities, record decisions in the spec |
| `/speckit.constitution` | Amend project principles |
| `/speckit.converge` | Reconcile conflicting artifacts |
| `/speckit.plan` | Generate the implementation plan |
| `/speckit.specify` | Create or update the feature specification |
| `/speckit.tasks` | Generate the task breakdown |
| `/speckit.checklist` | Verify spec quality items |
| `/speckit.taskstoissues` | Convert tasks to issues |
| `/speckit.implement` | Execute the task list |

**Testing standards** — unit tests are pure; integration tests replay `vcrpy` cassettes so no CI
run ever consumes a free-tier quota. Model inference in CI uses a tiny deterministic stub fixture
rather than downloading hundreds of megabytes of weights.

---

## Roadmap

Corresponds to spec §5; a milestone closes only when every FR in its column has a task *and* a
passing test.

| Milestone | Scope | FRs | Status |
|---|---|---|---|
| M1 | Repository structure & `.specify` guidelines | — | ✅ complete |
| M2 | Orchestration core & state persistence | FR-001, 002, 003, 016, 017 | 🎯 next |
| M3 | Ingestion adapters, rate limiting, caching | FR-004–008, 015, 020 | planned |
| M4 | Sentiment pipeline & model integration | FR-009, 010, 017 | planned |
| M5 | Knowledge graph schema, mutations, read access | FR-011–014, 021 | planned |
| M6 | Telemetry observability & evaluation | FR-017, 018, 019 | planned |

**Also open:** a constitution amendment is required for the Neo4j/Redis conflicts (see below).

---

## Open decisions

These are recorded in `plan.md` and `tasks.md` and are **not yet resolved**:

1. **Constitution amendment required** — the constitution's *Additional Constraints* names
   **Neo4j Community (GPLv3)** and **Redis (AGPLv3)** as permitted stores, contradicting Principle
   I. The plan substitutes DuckDB and an in-process token bucket, but the constitution text needs
   an explicit amendment outside the Spec Kit command chain.
2. **Spec cites FinBERT** (spec §2/§3.3) but the `ProsusAI/finbert` weights declare no license.
   The plan binds to Apache-2.0 checkpoints; the spec's "(e.g., FinBERT)" is illustrative and
   should ideally be updated.
3. **Redistribution rights** for provider data are unresolved (V4, above).
4. **Scale assumption is unconfirmed** — ~500 assets / ~3k news items/day is an estimate that
   drives store sizing and the ingestion schedule. Verified by T078.
5. **DBOS Python SQLite backend GA status** must be verified at pin time; Prefect 3 (Apache-2.0)
   is the documented fallback.
6. **Spec.md has no user stories** — the five stories in `tasks.md` are derived from the FR
   register and milestone order. The mapping is documented in `tasks.md`.
7. **Duplicate constitution files** — both `.specify/memory/constitution.md` and
   `.specify/specs/constitution.md` exist and are tracked. The `memory/` copy is authoritative;
   the other should be deleted or marked a generated mirror.

---

## License

Open source throughout. Every runtime dependency is MIT, Apache-2.0, BSD-style, or public
domain — enforced in CI by the license gate. See [Technology stack](#technology-stack) for the
per-dependency breakdown.
