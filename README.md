# Aetheris

**Financial Intelligence Knowledge Graph & Sentiment Analysis Engine**

Aetheris ingests macroeconomic data, asset prices, and financial news; scores
unstructured text with open-source financial NLP models; and structures
everything into a dynamic, queryable Knowledge Graph — all under a strict
open-source-only constraint.

---

## Status

Under active construction. The specification, plan, and task breakdown are
complete, and the durable-execution and ingestion layers are implemented.

| Work item | Progress | Tasks |
|---|---|---|
| Pipeline (`src/`) | 41 / 81 | durable execution, ingestion adapters |
| Documentation site | 12 / 34 | site builds, API reference generates, gates enforce |

This README is kept in sync automatically: CI runs `scripts/check_readme_sync.py`
and **fails the build if any factual claim here is stale**. If you change
behaviour, expect that check to fail until you update the README.

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [The five decoupled layers](#the-five-decoupled-layers)
- [Technology stack](#technology-stack)
- [Repository layout](#repository-layout)
- [Installation](#installation)
- [CLI](#cli)
- [Python API](#python-api)
- [Documentation site](#documentation-site)
- [Data model](#data-model)
- [Point-in-time correctness](#point-in-time-correctness)
- [Performance targets](#performance-targets)
- [Constitution](#constitution)
- [Provider terms & legal boundaries](#provider-terms--legal-boundaries)
- [Development](#development)
- [Verification](#verification)
- [Open decisions](#open-decisions)

---

## What it does

1. **Ingest** macro series, prices, and news from five free-tier providers.
2. **Score** unstructured news text into calibrated sentiment metrics.
3. **Align** indicators and sentiment into one point-in-time-correct timeline.
4. **Upsert** everything idempotently into a typed Knowledge Graph.
5. **Observe and evaluate** — distributed traces, metrics, and drift evaluation.

The pipeline runs as a **durable workflow**: on failure it resumes from the last
completed step with bounded exponential backoff, and failed steps run
compensating actions.

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
│   Workers     │──text───▶│     Engine      │──score▶│     Engine       │
│               │          │                 │        │                  │
│ fred_client   │          │  analyzer       │        │  node upsert     │
│ market_client │          │  calibration    │        │  edge upsert     │
│ news_client   │          │                 │        │  as-of align     │
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

Every arrow is a **hard boundary enforced in CI** by `import-linter`:
`ingestion`, `sentiment`, and `graph` never import `orchestration`; `sentiment`
never imports `graph`; `graph` never imports `ingestion`.

---

## The five decoupled layers

| # | Layer | Path | Responsibility |
|---|-------|------|----------------|
| 1 | Workflow Orchestrator | `src/orchestration/` | Durable state machines, checkpoint/resume, bounded retries, compensation |
| 2 | Ingestion Workers | `src/ingestion/` | Modular provider adapters, shared token-bucket limiting, TTL caching |
| 3 | Sentiment Engine | `src/sentiment/` | Isolated CPU inference producing calibrated polarity scores |
| 4 | Knowledge Graph Engine | `src/graph/` | Typed nodes and weighted edges, idempotent upserts, read-only queries |
| 5 | Observability & Evaluation | `src/observability/` | Distributed spans, metric collection, drift evaluation |

Each layer is independently importable and replaceable.

---

## Technology stack

**Code**: Python 3.11+, CPU-only (no GPU).

**Runtime dependencies** (all permissively licensed — see
[`research.md`](.specify/specs/research.md)):

| Concern | Choice | License |
|---|---|---|
| Durable orchestration | DBOS | MIT |
| Graph storage | DuckDB | MIT |
| Raw payloads / journal | SQLite | public domain |
| Sentiment model | FinSense-ModernBERT (primary), distilroberta-financial (fallback), VADER (baseline) | Apache-2.0 / Apache-2.0 / MIT |
| Telemetry | OpenTelemetry + Jaeger | Apache-2.0 |
| Metrics | `prometheus_client` | Apache-2.0 |
| Evaluation | MLflow + Evidently | Apache-2.0 |
| Testing | pytest, vcrpy | MIT |
| Tooling | ruff, mypy, import-linter | MIT / MIT / BSD |

**Documentation toolchain** ([`research.md`](.specify/specs/001-docs-site-cicd/research.md)):

| Concern | Choice | License |
|---|---|---|
| Site generator | MkDocs + Material | BSD-2-Clause / MIT |
| API reference | mkdocstrings + gen-files + literate-nav | ISC / MIT / MIT |
| Accessibility (static) | `html-validate` | MIT |
| Accessibility (rendered) | bespoke Playwright checker | Apache-2.0 |

### Notable exclusions

Rejected on licence grounds:

| Excluded | Why |
|---|---|
| Neo4j Community Edition | GPLv3 — copyleft |
| Redis (8.x) | AGPLv3 |
| Grafana / Grafana Tempo | AGPL-3.0 |
| Uptrace | AGPL-3.0 |
| ClearML server | SSPL 1.0 |
| FalkorDB | SSPLv1 |
| Memgraph | BSL 1.1 |
| Restate | BSL 1.1 |
| TypeDB CE | MPL-2.0 |
| Kùzu | Frozen — repo archived after Apple acquisition |
| ProsusAI/finbert, yiyanghkust/finbert-tone | **No license declared for the model weights** |
| Loughran–McDonald lexicon | Academic use only |
| `axe-core` | MPL-2.0 — the WCAG 2.1 AA gate is composed from two permissive gates instead |
| `pdoc3` | AGPL-3.0 |

---

## Repository layout

```
.
├── .github/workflows/
│   ├── ci.yml                   # build, test, licence gate, docs gates
│   └── docs.yml                 # GitHub Pages deploy (feature 001-docs-site-cicd)
├── docs/                        # documentation site source
│   ├── index.md · installation.md
│   ├── usage/                   # CLI reference, accessibility
│   ├── stylesheets/ · overrides/
│   └── reference/               # GENERATED — never hand-edited
├── scripts/                     # build and maintenance tooling
├── src/
│   ├── config/                  # typed settings, provider registry
│   ├── orchestration/           # workflows, checkpoints, retry, compensation
│   ├── ingestion/               # adapters, cache, rate limiter, payloads
│   ├── sentiment/               # analyzer, calibration, schema
│   ├── graph/                   # schema, repository, align, queries, api
│   ├── observability/           # telemetry, metrics, evaluate
│   └── cli/                     # argparse entrypoint, licence gate
├── tests/
│   ├── unit/ · contract/ · integration/
│   └── fixtures/                # cassettes, stub model, a11y fixture
├── mkdocs.yml
├── package.json                 # docs toolchain only
├── pyproject.toml
└── .specify/
    ├── memory/                  # constitution, amendments
    ├── specs/                   # pipeline + feature design artifacts
    └── scripts/ · templates/
```

---

## Installation

**Language/Version**: Python 3.11+ (no GPU required).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### First run — no credentials needed

```bash
aetheris run --trigger schedule --dry-run
```

A dry run validates configuration and quotas without calling any provider, so it
needs no API keys and consumes no free-tier quota.

### Live runs

Set credentials in the environment — never commit them:

```bash
export FRED_API_KEY="..."
export FINNHUB_API_KEY="..."
export FMP_API_KEY="..."
export TIINGO_API_KEY="..."
export ALPHA_VANTAGE_API_KEY="..."

aetheris run --trigger schedule
```

---

## CLI

**6 commands**: `run`, `ingest`, `status`, `version`, `check-licenses`, and
`docs`. The `query` command is specified but not yet implemented (task T064).

Global options (`--config`, `--db`, `--raw`, `--json`, `--trace`) work either
before or after the subcommand.

| Command | Purpose |
|---|---|
| `aetheris run` | Execute the durable pipeline. `--dry-run`, `--resume ID`, `--trigger schedule\|event` |
| `aetheris ingest` | Run one adapter. `--param KEY=VALUE`, `--data-type`, `--force`, `--ttl` |
| `aetheris docs` | `build` (all gates), `serve` (localhost preview), `open` |
| `aetheris check-licenses` | Constitution Principle I gate. `--strict`, `--lenient`, `--no-exemptions` |
| `aetheris status` | Provider health, quotas, cache hit ratio, workflow state |

**Exit codes**: `0` success · `1` runtime failure · `2` usage error · `3` gate failure.

Full reference: [`docs/usage/cli.md`](docs/usage/cli.md) (once the site is built)
or [`cli.md`](.specify/specs/contracts/cli.md).

---

## Python API

The graph layer exposes a **read-only, in-process** API — no network service, no
auth surface (clarification: no web service is in scope).

```python
from src.graph.api import get_entity, sentiment_for, top_influencers
from datetime import datetime, timezone

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

Contracts: [`graph-api.md`](.specify/specs/contracts/graph-api.md) ·
[`sentiment-schema.md`](.specify/specs/contracts/sentiment-schema.md) ·
[`adapter-protocol.md`](.specify/specs/contracts/adapter-protocol.md).

Generated reference: `aetheris docs build` then open `site/reference/`.

---

## Documentation site

MkDocs + Material, with the API reference **generated from source docstrings**.
Navigation is generated too, so adding a module requires no manual edit.

```bash
pip install -e ".[docs]"
npm install
npx playwright install chromium        # one-time, ~100MB

aetheris docs serve                    # http://127.0.0.1:8000
aetheris docs build                    # build + run all gates
```

**Gates, in order** (a failure at any step stops the build):

| # | Gate | Enforces |
|---|---|---|
| 1 | Docstring coverage | every public entity documented |
| 2 | Site build | `--strict`; any warning fails |
| 3 | Landmark labels | fixes duplicate `aria-label`s from the theme |
| 4 | Accessibility — structural | `html-validate` |
| 5 | Accessibility — rendered | computed contrast, heading order |

**Read [`docs/usage/accessibility.md`](docs/usage/accessibility.md) before trusting
an accessibility claim.** It lists exactly which WCAG criteria are verified and
which are *not*, including three focus criteria that could not be checked
reliably against the theme.

---

## Data model

**Nodes**

| Node | Identity | Key fields |
|---|---|---|
| `Asset` | `asset_id` = `class:exchange:symbol` | `ticker`, `name`, `asset_class`, `currency` |
| `MacroIndicator` | `indicator_code` (provider-prefixed) | `name`, `unit`, `frequency` |
| `SentimentMetric` | `metric_id` = `entity:source:text_hash` | `score` ∈ [-1, 1], `confidence` ∈ [0, 1] |

**Edges** — all carry a signed `weight` ∈ [-1, 1] and `confidence` ∈ [0, 1]:

| Edge | Meaning |
|---|---|
| `INFLUENCES` | signed influence strength (negative is meaningful) |
| `CORRELATES_WITH` | Pearson correlation coefficient |
| `TARGETS` | strength of the sentiment-to-entity link |

Re-scoring with a new model creates a **new** row rather than overwriting,
preserving lineage.

Full specification: [`data-model.md`](.specify/specs/data-model.md).

---

## Point-in-time correctness

The single most important rule:

> All alignment across disparate frequencies uses a **backward as-of join** with
> a strict **no-lookahead invariant** — a data point may only observe values
> already published at its own timestamp.

A CPI print released on the 12th covering January is *not* observable by a graph
state on January 31. Conflating **observation time** with **availability time**
is the classic way a pipeline leaks future information and silently invalidates
every downstream query.

This rule is **specified but not yet implemented**. The alignment layer and the
deterministic replay test are tasks T060 and T054, both in the knowledge graph
user story. Until they land, the invariant is enforced by review rather than by
a test — exactly the gap the remaining tasks close.

---

## Performance targets

| ID | Target |
|---|---|
| SC-001 | P95 scheduled run < 5 minutes |
| SC-002 | Failed workflow resumes in < 60 seconds |
| SC-003 | Cache hit ratio ≥ 80% at steady state |
| SC-004 | Documentation build < 60 seconds |

Scale assumption (unconfirmed): ~500 assets, ~25 macro series, ~3,000 news
items/day.

---

## Constitution

Five non-negotiable principles: [`.specify/memory/constitution.md`](.specify/memory/constitution.md).

1. **Strict Open-Source Compliance** — MIT/Apache-2.0/BSD only, no paid tiers or SaaS
2. **Decoupled Modular Architecture** — every layer independently importable
3. **Resilient Durable Execution** — checkpointing, backoff, compensation
4. **Comprehensive Observability & Evaluation** — spans on every operation; drift evaluated
5. **Specification-Driven Rigor** — no code without a spec update and task breakdown

**Every PR must pass** `aetheris check-licenses` (Principle I) and `lint-imports`
(Principle II). Accepted deviations are documented in
[`constitution-amendments.md`](.specify/memory/constitution-amendments.md) — four
non-permissive **transitive** dependencies (`psycopg`, `psycopg-binary`,
`certifi`, `huey`) and `pathspec`, all accepted by explicit decision rather than
silently. `--no-exemptions` re-checks every one.

---

## Provider terms & legal boundaries

Aetheris consumes provider data **internally** and exports only its own derived
artifacts (scores, weights, edges). Three of the five mandated providers
explicitly forbid redistribution on free tiers:

- **Finnhub** — "Personal Use"
- **Financial Modeling Prep** — requires a separate Display & Licensing Agreement
- **Tiingo** — "internal and personal use only… may not redistribute"

**Do not ship redistributed derived data without written confirmation from these
providers.** FRED additionally requires a visible attribution string.

---

## Development

Workflow is **specification-first**: no code without a matching spec update and
task breakdown (Principle V).

```
spec.md ──▶ clarify ──▶ plan.md ──▶ tasks.md ──▶ implement
```

**Adding a feature:**

1. Update `.specify/specs/spec.md` with a new FR ID (register in §5.2)
2. Re-run the Spec Kit commands for `plan.md` and `tasks.md`
3. Write the failing test first, then implement (red-green-refactor)
4. Confirm `lint-imports` and the licence gate pass before marking done

**Testing standards** — unit tests are pure; integration tests replay `vcrpy`
cassettes or scripted fakes so no CI run ever consumes free-tier quota.

Spec Kit commands: `/speckit.analyze` · `/speckit.clarify` · `/speckit.plan` ·
`/speckit.specify` · `/speckit.tasks` · `/speckit.implement`

---

## Verification

**5 import contracts kept** · **156 tests pass** · **20 API reference pages**

```bash
pytest tests/                              # unit, contract, integration
ruff check src/ tests/ scripts/            # lint + docstring gate
lint-imports                               # Principle II boundaries
aetheris check-licenses --lenient          # Principle I
aetheris docs build                        # all five documentation gates
```

Pre-commit expectations: `ruff` and the docstring gate (`D1xx` rules) must pass,
because the documentation site generates from those docstrings and a missing one
fails the build.

---

## Open decisions

These remain unresolved — recorded in [`plan.md`](.specify/specs/plan.md) rather
than hidden:

- **Constitution amendment** — Neo4j Community (GPLv3) and Redis (AGPL) are still
  named as permitted stores in the constitution, contradicting Principle I. The
  plan substitutes DuckDB and an in-process token bucket, but the text needs
  amending. *(I introduced that line during an earlier remediation; it is my
  error to own.)*
- **Amendment A-001 is DRAFT** — Principle I is knowingly weakened for five
  transitive dependencies pending ratification.
- **Spec cites FinBERT**, whose weights declare no licence. The implementation
  binds to Apache-2.0 checkpoints instead.
- **Redistribution rights** for provider data are unresolved.
- **Scale assumption is unconfirmed** (~500 assets / ~3k news items/day).
- **DBOS Python SQLite backend GA status** must be verified at pin time.
- **`spec.md` has no user stories** — the five in `tasks.md` are derived from the
  FR register and milestone order.
- **Duplicate constitution files** — both `.specify/memory/constitution.md` and
  `.specify/specs/constitution.md` are tracked.

---

## Licence

Open source throughout. Every runtime dependency is MIT, Apache-2.0, BSD-style,
ISC, or public domain — enforced in CI by the licence gate. See
[Technology stack](#technology-stack) for the per-dependency breakdown.
