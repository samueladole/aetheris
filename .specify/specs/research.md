# Phase 0 Research: Aetheris

**Date**: 2026-10-10 | **Spec**: [`./spec.md`](./spec.md) | **Plan**: [`./plan.md`](./plan.md)

Every `NEEDS CLARIFICATION` in Technical Context was resolved by targeted research against the
constitution's binding constraint: **permissive licenses only (MIT / Apache-2.0 / BSD / public
domain), no paid tiers, self-hosted, zero lock-in.** Decisions below follow the
*Decision → Rationale → Alternatives considered* format.

---

## A. Durable Workflow Orchestration

### Decision
Use **DBOS Transact (`dbos-transact`), MIT** — a durable-execution Python library with a SQLite step
journal, `max_attempts` + `backoff_rate` exponential retries, `timeout_seconds` per step, and
automatic resume from the journal.

### Rationale
It is the only candidate that is simultaneously (a) permissively licensed, (b) a true *in-process
embeddable* library — matching the constitution's modular-worker and "no heavy framework dependency"
requirements — and (c) ships checkpointing, bounded retries, and activity timeouts natively. SQLite is
its default durability backend, so macOS dev needs no server, scheduler, or cluster.

### Alternatives considered
| Candidate | License | Why rejected |
|---|---|---|
| Prefect 3 | Apache-2.0 | Fallback (kept). Real orchestration drifts toward a self-hosted Prefect server; conflicts with the in-process constraint |
| Temporal (server + Python SDK) | MIT | Best-in-class durability and native saga, but requires a Go server + Postgres/Cassandra — server-coupled |
| Apache Airflow 3 | Apache-2.0 | Requires server + DB + scheduler; far too heavy for a 5-layer pipeline |
| Dagster | Apache-2.0 | No per-step resume; needs webserver + daemon |
| Luigi | Apache-2.0 | Stale; limited retry/timeout/compensation |
| Netflix Conductor, Cadence, Argo Workflows | Apache-2.0 | Server/K8s-coupled; architecturally wrong |
| **Restate** | **BSL 1.1** | **Disqualified — source-available, not OSI open source** |
| Build-your-own SQLite state machine | — | Rejected: DBOS already provides the journal; a hand-rolled state machine is more code and more test surface for identical behavior |

### Residual risk
DBOS ships **no saga DSL**, so compensation must be written explicitly per workflow
(`try/except` + compensating call). This is acceptable and gives full control, but it means
FR-003's failure path needs dedicated tests. **Open risk:** confirm the Python SQLite backend is GA
in the pinned version; if not, fall back to Prefect 3 (Apache-2.0).

---

## B. Knowledge Graph Storage

### Decision
Use **DuckDB, MIT**, embedded, with nodes and edges as typed tables and
`INSERT … ON CONFLICT` for idempotent upsert. The graph layer is relational-by-design but
graph-shaped, accessed through `src/graph/repository.py`.

### Rationale
v1 queries are shallow (1–2 hop: "sentiment for asset X in window W", "edges of type T for X above
weight θ", "top influencers of X"). DuckDB is the most mature permissively-licensed embedded store
available, gives real persistence, ACID upsert, and recursive CTEs for the occasional multi-hop path
— with zero vendor risk. The constitution's "complexity must be avoided in favor of simplicity and
YAGNI" rule applies directly here.

### Alternatives considered
| Candidate | License | Why rejected |
|---|---|---|
| **Neo4j Community** | **GPLv3** | **Disqualified** — copyleft, contradicts Principle I; also requires a server process, failing the no-network-service requirement. The Apache-2.0 Python driver does not launder the engine's license |
| **Kùzu** | MIT | **Frozen** — acquired by Apple, repo archived (last release 0.11.3, Oct 2025); no security patches |
| **LadybugDB** | MIT | Active community successor to Kùzu (embedded, openCypher, `MERGE`). **Deferred**, not rejected: ~1 year old, and the `ladybug` PyPI name collides with an unrelated **AGPL-3.0** project, so the dependency must be pinned and audited before adoption. Revisit at Milestone 5 exit if multi-hop traversal becomes a real requirement |
| NetworkX | BSD-3-Clause | In-memory only, no persistence, and upsert is application code — fails the durability requirement |
| SQLite | Public domain | Viable fallback (see below), but weaker analytical/recursive query ergonomics than DuckDB |
| DuckDB | MIT | — |
| TypeDB CE | MPL-2.0 / AGPL protocol | Disqualified — not permissive |
| FalkorDB | SSPLv1 | Disqualified — service copyleft, explicitly banned |
| Memgraph | BSL 1.1 | Disqualified — "not an open source license"; converts to Apache-2.0 only in 2030 |
| JanusGraph, HugeGraph, TerminusDB | Apache-2.0 | All require a server or JVM — fails embedded/no-service |
| TinkerPop, CogDB | Apache-2.0 / MIT | Framework, or too immature to carry the data model |

### Fallback
If LadybugDB passes a dependency audit *and* a multi-hop requirement materializes, migrate behind the
existing `src/graph/repository.py` interface — the contract is already isolation-ready.

---

## C. Observability & Evaluation Stack

### Decision
A three-component, all-Apache-2.0 stack:
1. **OpenTelemetry Python SDK + OTLP → Jaeger** (both Apache-2.0) — the open telemetry standard the
   constitution names explicitly.
2. **`prometheus_client` → Prometheus** (Apache-2.0) — latency/throughput/error counters and gauges.
3. **MLflow** (Apache-2.0, self-hosted SQLite backend) — experiment/run tracking, model outputs,
   confidence metrics, and model registry.

Plus **Evidently** (Apache-2.0) computed inside the evaluation step for data drift, with results
written to MLflow + a Prometheus gauge + an OTel span event.

### Rationale
Satisfies Principle IV literally: every workflow execution, ingestion fetch, sentiment inference, and
graph mutation emits OTel spans, and model outputs/confidence/drift are logged to an OSS tracker.
MLflow's core Tracking, Evaluation, and Tracing are all Apache-2.0 OSS — the Databricks-only items
are governance/HA/DR/Unity Catalog, none of which this project needs. MLflow Tracing is OTel-native,
so it points at the same Jaeger backend.

### Alternatives considered
| Candidate | License | Why rejected |
|---|---|---|
| Grafana / Grafana Tempo | **AGPL-3.0** | **Disqualified** — relicensed 2021 to AGPL |
| Uptrace | **AGPL-3.0** | **Disqualified** |
| ClearML (server) | **SSPL 1.0** | **Disqualified** — `clearml-server` bundles MongoDB SSPL |
| SigNoz | MIT core + proprietary `ee/` | Usable but feature carve-outs make compliance auditing messy; skipped for simplicity |
| Apache SkyWalking | Apache-2.0 | Compliant fallback if a single pane of glass is ever required; heavier, JVM-oriented |
| Zipkin | Apache-2.0 | Jaeger supersedes it and both ingest OTLP natively |
| W&B self-hosted | MIT (client) | Self-hosted *production* requires requesting a license from wandb.com — a mild vendor gate |
| Neptune | Mixed | Self-host is enterprise/paid |
| Aim / whylogs / Great Expectations | Apache-2.0 | Compliant alternates; MLflow covers tracking + eval in one component, so these were merged out per YAGNI |
| structlog / Loguru | MIT | OTel logs preferred for cross-signal correlation; structlog retained only as a local console formatter |

---

## D. Sentiment Model & Runtime

### Decision
**Model**: `AnkitAI/FinSense-ModernBERT-Financial-News-Sentiment-Analysis` (Apache-2.0 weights, 149M
params, 8192 context — full news bodies fit without chunking) as primary, with
`mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis` (Apache-2.0, 82M params) as the
validated fallback and `vaderSentiment` (MIT) as a deterministic cold-start baseline.

**Mapping**: `polarity_score = P(positive) − P(negative)`; `confidence = max(P(pos), P(neg), P(neu))`,
then **temperature-scaled** on a held-out Financial PhraseBank split before exposing confidence.

**Runtime**: `transformers` (Apache-2.0) + `tokenizers` (Apache-2.0) + `safetensors` (Apache-2.0) +
`torch` (BSD-style) on CPU. Optional INT8 export via `optimum`/`onnxruntime` (both permissive).
**No GPU required.**

### Rationale
Only these checkpoints satisfy Principle I for *weights as well as code*: `ProsusAI/finbert`,
`yiyanghkust/finbert-tone`, `ahmedrachid/FinancialBERT-Sentiment-Analysis`, and
`cardiffnlp/twitter-roberta-base-sentiment` all declare **no license** for their weights on Hugging
Face, which under HF's terms means all rights reserved. The spec's "(e.g., FinBERT)" is illustrative,
so no spec amendment is required, but the model manifest in `src/sentiment/models.py` is the
enforcement point.

### Alternatives considered
| Candidate | License | Why rejected |
|---|---|---|
| **ProsusAI/finbert** | **No weight license** | **Disqualified** — all rights reserved under HF terms (note: it is the model named in the spec, so see V3) |
| **yiyanghkust/finbert-tone** | **No weight license** | **Disqualified** — the `yya518/FinBERT` code repo is Apache-2.0, but the HF weights are separately unlicensed |
| ahmedrachid/FinancialBERT… | No weight license | Disqualified |
| cardiffnlp/twitter-roberta… | No weight license | Disqualified |
| mrm8488/deberta-v3-ft-financial | MIT | Compliant but needs `sentencepiece`; slower on macOS BLAS than the primary |
| nlptown/bert-base-multilingual | MIT | Not financial-domain; kept only if non-English news enters scope |
| vLLM | Apache-2.0 | LLM serving engine — overhead with no benefit for encoder classification |
| **Loughran–McDonald Master Dictionary** | **Academic use only** | **Disqualified** — commercial license required |
| VADER | MIT | Fastest and deterministic, but too weak as a primary; retained as fallback/baseline |

### Residual risk
The primary model's accuracy is benchmarked **only by its own author** on Financial PhraseBank
(FPB); the authors themselves note `ProsusAI/finbert` scores higher on FPB, likely from benchmark
memorization. Verify against our own held-out set during Milestone 4, and keep the
`distilroberta` fallback wired behind the same interface.

---

## E. Provider Free-Tier Terms (v1 constraint, not a license issue)

| Provider | Rate limit | Daily cap | Commercial / redistribution |
|---|---|---|---|
| FRED | 120 req/min | none | Commercial use OK; **redistribution of third-party-copyrighted series restricted** + attribution string required |
| Alpha Vantage | 25 req/day (free) | 25 | Least restrictive on redistribution, but the quota is effectively unusable for real ingestion volume without the open-source-project exception (application required) |
| Finnhub | 60 req/min (+30/sec global) | none | ❌ Free tier labelled **"Personal Use"** |
| FMP | 250 calls/30 days tier | 250/day | ❌ Redistribution requires a **Data Display and Licensing Agreement** |
| Tiingo | 50 req/hour | 1000/day | ❌ **"internal and personal use only… you may not redistribute the data in any form"** |

### Consequence for the design
These terms sit inside Principle I's mandate to use exactly these five providers. v1 therefore
**consumes** provider data internally and exports only Aetheris' own derived artifacts (sentiment
scores, weights, edges). Redistribution of derived data needs written confirmation — **FMP in
particular** — before any external distribution. Recorded as **V4** in `plan.md`.

---

## F. Decisions carried into `data-model.md` and `contracts/`

- Graph identity keys, weight/confidence scales, and the no-lookahead invariant are specified in
  [`data-model.md`](./data-model.md).
- Public surfaces (read-only API, CLI, sentiment payload schema, adapter protocol) are specified in
  [`contracts/`](./contracts/).
