# Aetheris

**Financial Intelligence Knowledge Graph & Sentiment Analysis Engine**

Aetheris ingests macroeconomic data, asset prices, and financial news; scores
unstructured text with open-source financial NLP models; and structures
everything into a dynamic, queryable Knowledge Graph — all under a strict
open-source-only constraint.

---

## Status

Every task in both breakdowns is checked off, but **the pipeline does not run
end to end yet**. All five layers are built and tested on their own; the steps
that connect them are placeholders, and nothing writes to the knowledge graph
outside tests. [Known gaps](#known-gaps) lists exactly what is missing.

| Work item | Progress | Tasks |
|---|---|---|
| Pipeline (`src/`) | 81 / 81 | durable execution, ingestion, sentiment, graph, observability |
| Documentation site | 34 / 34 | builds and gates enforce; publishing waits on GitHub Pages being enabled (see Known gaps) |

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
- [Design documents](#design-documents)
- [Development](#development)
- [Verification](#verification)
- [Known gaps](#known-gaps)
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

That is the design. Today the workflow runs all five steps durably, but steps
2–5 return zero counts instead of calling their layers. Each layer works when
called directly; see [Known gaps](#known-gaps).

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
│   ├── validation.md            # recorded gate results
│   └── reference/               # GENERATED — never hand-edited
├── scripts/                     # build, maintenance, and gate tooling
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
├── .env.example                 # credential template (.env is git-ignored)
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
pip install torch --index-url https://download.pytorch.org/whl/cpu   # Linux only
pip install -e ".[dev]"
```

**On Linux, install CPU-only torch first.** PyPI's Linux `torch` is the CUDA
build, which pulls in NVIDIA's proprietary CUDA wheels, so a plain install
breaks Principle I. With uv, `uv pip install -e ".[dev]" --torch-backend cpu`
does both steps. macOS wheels are CPU-only already.

### First run — no credentials needed

```bash
aetheris run --trigger schedule --dry-run
```

A dry run validates configuration and quotas without calling any provider, so it
needs no API keys and consumes no free-tier quota.

### Credentials

Credentials can come from the environment or from a local `.env` file, which is
git-ignored. A key already exported in the environment **always wins** over the
file, so a stale `.env` can never silently shadow a credential you set
deliberately.

```bash
cp .env.example .env      # then fill in real values
```

`.env` is read automatically from the working directory. To point elsewhere:

```bash
export AETHERIS_ENV_FILE=./.env.secrets
```

A malformed or unreadable `.env` produces a warning and the run continues with
whatever credentials it does have — a corrupted local file never breaks the CLI.
Run `aetheris status` to see which providers are configured.

### Live runs

```bash
aetheris run --trigger schedule
```

---

## CLI

**9 commands**: `run`, `ingest`, `score`, `graph`, `evaluate`, `status`, `version`,
`check-licenses`, and `docs`. Read-only graph queries run as
`aetheris graph query entity|sentiment|edges|influencers|assets`. The CLI
contract specifies them as a top-level `aetheris query`; the implementation
nests them under `graph`.

Global options (`--config`, `--db`, `--raw`, `--json`, `--trace`) work either
before or after the subcommand.

| Command | Purpose |
|---|---|
| `aetheris run` | Execute the durable pipeline (data steps are placeholders). `--dry-run`, `--resume ID`, `--trigger schedule\|event` |
| `aetheris ingest` | Run one adapter and store the raw payload. `--param KEY=VALUE`, `--data-type`, `--force`, `--ttl` |
| `aetheris score` | Score literal `--text` and print the result. `--entity`, `--model`, `--temperature` |
| `aetheris graph` | `upsert` (placeholder: counts raw payloads, writes nothing) and read-only `query` |
| `aetheris evaluate` | Score summaries, drift, MLflow logging |
| `aetheris docs` | `build` (all gates), `serve` (localhost preview), `open` |
| `aetheris check-licenses` | Constitution Principle I gate. `--strict`, `--lenient`, `--no-exemptions` |
| `aetheris status` | Provider health, quotas, cache hit ratio, workflow state |

**Exit codes**: `0` success · `1` runtime failure · `2` usage error · `3` gate failure.

Full reference: [`docs/usage/cli.md`](docs/usage/cli.md) (once the site is built)
or [`cli.md`](.specify/specs/contracts/cli.md).

---

## Python API

### Working today — sentiment

```python
from src.sentiment.analyzer import score_text
from src.sentiment.schema import build_payload

# Score text and get a signed polarity plus calibrated confidence.
# `float(polarity)` works because Polarity *is* a float.
polarity, probabilities, model = score_text(
    "Company beats expectations as revenue surges", temperature=2.0
)
print(float(polarity), polarity.confidence)
```

Polarity is signed (`P(pos) - P(neg)`) and confidence is **temperature-scaled** — a
raw transformer softmax is systematically overconfident, so passing it through
uncalibrated would violate VR-2. Model choice is gated by a licence manifest: any
model not listed in `src/sentiment/models.py` is refused rather than trusted.

### Also working — read-only graph queries

The graph layer exposes a **read-only, in-process** API (no network service, no
auth surface — clarification: no web service is in scope):

```python
from src.graph.api import get_entity, sentiment_for, top_influencers
from datetime import datetime, timezone

points = sentiment_for(
    "equity:NASDAQ:AAPL",
    start=datetime(2026, 10, 1, tzinfo=timezone.utc),
    end=datetime(2026, 10, 15, tzinfo=timezone.utc),
    min_confidence=0.7,
)
```

Traversal is capped at two hops; deeper returns ``UnsupportedTraversalError``
rather than silently truncating. Same queries from the CLI:
`aetheris graph query sentiment --entity ... --start ... --json`.

Nothing in the pipeline writes to the graph yet, so these queries return empty
results unless you populate it yourself through `GraphRepository` in
`src/graph/repository.py`.

Contracts: [`graph-api.md`](.specify/specs/contracts/graph-api.md) ·
[`sentiment-schema.md`](.specify/specs/contracts/sentiment-schema.md) ·
[`adapter-protocol.md`](.specify/specs/contracts/adapter-protocol.md).

Generated reference for everything implemented: `aetheris docs build`, then open
`site/reference/`.

---

## Documentation site

MkDocs + Material, with the API reference **generated from source docstrings**.
Navigation is generated too, so adding a module requires no manual edit. The
deploy workflow publishes to GitHub Pages on every push to `main` once Pages is
enabled for the repository (see [Known gaps](#known-gaps)).

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
| 3 | Built-HTML repair | fixes duplicate `aria-label`s and missing `<th scope>` that the theme and generator introduce |
| 4 | Secret scan | no credential in the published output — the site is public |
| 5 | Accessibility — structural | `html-validate` |
| 6 | Accessibility — rendered | computed contrast, heading order |

**Deployment** preserves the two properties that fail silently when wrong: it
triggers on `main` only, and the publish job declares `needs: build`, so a
failed build never reaches the live site. Both are asserted by
`tests/contract/test_docs_workflow.py`.

**Read [`docs/usage/accessibility.md`](docs/usage/accessibility.md) before
trusting an accessibility claim.** It lists exactly which WCAG criteria are
verified and which are *not* — including three focus criteria that could not be
checked reliably against the theme, and two theme defects. Rules outside the
covered list are *unverified, not compliant*.

Validation results are recorded in [`docs/validation.md`](docs/validation.md).

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

The join itself is implemented in `src/graph/align.py` and tested in
`tests/unit/test_align.py`: a value released after the query time is invisible
to it, timezone-naive times are refused, and aligning the same inputs twice
gives identical rows. Two parts of the guarantee are still missing:

- **No write path calls the join.** The graph repository refuses
  timezone-naive timestamps, but no code routes data through `align()`
  outside its tests.
- **There is no end-to-end replay test.** T054 calls for an integration test
  replaying a cassette to a byte-identical *graph* state. The unit tests above
  cover alignment only.

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
[`constitution-amendments.md`](.specify/memory/constitution-amendments.md) — five
non-permissive **transitive** dependencies (`psycopg`, `psycopg-binary`,
`certifi`, `huey`, `tqdm`) and `pathspec`, all accepted by explicit decision
rather than silently. `--no-exemptions` re-checks every one.

The gate fails closed: a licence it cannot identify fails strict mode rather
than passing. Where a package's metadata names no licence, the licence is
verified by hand against its LICENSE file and recorded in `VERIFIED_LICENSES`
in `src/cli/licenses.py` (currently `skops`), and every report lists it.

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

## Design documents

Everything that explains *why* the code is shaped this way lives in
`.specify/`, not in this README. The pipeline is built specification-first:
the artefacts are generated from each other in order, and the code follows the
tasks.

```
spec.md ──▶ clarify ──▶ plan.md ──▶ tasks.md ──▶ implement
```

### Project-wide

| Document | What it holds |
|---|---|
| [`constitution.md`](.specify/memory/constitution.md) | The five non-negotiable principles |
| [`constitution-amendments.md`](.specify/memory/constitution-amendments.md) | Accepted deviations, and decisions recorded so they are not re-litigated |
| [`spec.md`](.specify/specs/spec.md) | Requirements FR-001…FR-021, success criteria, provider registry |
| [`plan.md`](.specify/specs/plan.md) | Architecture, technical context, constitution gates |
| [`research.md`](.specify/specs/research.md) | Licence-verified tooling decisions |
| [`data-model.md`](.specify/specs/data-model.md) | Entities, identity keys, validation rules VR-1…VR-9 |
| [`tasks.md`](.specify/specs/tasks.md) | 81 tasks traceable to FR IDs |
| [`quickstart.md`](.specify/specs/quickstart.md) | End-to-end validation guide |
| [`contracts/`](.specify/specs/contracts/) | graph-api, cli, sentiment-schema, adapter-protocol |

### Per-feature

Each feature gets its own directory under
[`.specify/specs/`](.specify/specs/) with the same artefact chain:

| Feature | Focus |
|---|---|
| [`001-docs-site-cicd/`](.specify/specs/001-docs-site-cicd/) | Documentation site and CI/CD |

### Two things worth knowing before you read them

1. **The duplicate constitution is unresolved.** Both
   `.specify/memory/constitution.md` and `.specify/specs/constitution.md` are
   tracked, and they have drifted. The `memory/` copy is authoritative — the
   one Spec Kit commands read. The other is a leftover tracked before the
   convention was settled (see [Open decisions](#open-decisions)).
2. **`spec.md` has no user stories.** It is organised by milestones and an FR
   register, so the five user stories in `tasks.md` are *derived* from those,
   not written in the spec. The mapping is documented in
   [`tasks.md`](.specify/specs/tasks.md).

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

**5 import contracts kept** · **470 tests** · **29 API reference pages**

```bash
pytest tests/                              # unit, contract, integration
ruff check src/ tests/ scripts/            # lint + docstring gate
lint-imports                               # Principle II boundaries
aetheris check-licenses --lenient          # Principle I (Python)
node scripts/check_licenses.js             # Principle I (Node — docs toolchain)
aetheris docs build                        # all six documentation gates
python scripts/check_readme_sync.py        # this README is up to date
```

Pre-commit expectations: `ruff` and the docstring gate (`D1xx` rules) must pass,
because the documentation site generates from those docstrings and a missing one
fails the build.

**Two licence gates, not one.** The Python gate walks installed Python
distributions; the Node gate walks `node_modules`. Neither alone covers the
toolchain, and a gate that silently skips half of it is not a gate.

---

## Known gaps

Every task in [`tasks.md`](.specify/specs/tasks.md) is checked off, but these
deliverables are missing or partly built. They are listed here so the task
count does not hide them.

- **The durable pipeline is a shell.** `aetheris run` executes all five steps
  with checkpointing and retries, but the ingest, sentiment, graph, and
  evaluation steps in `src/orchestration/workflows.py` return zero counts
  instead of calling their layers.
- **Nothing writes to the knowledge graph.** Only tests call the upsert
  methods on `GraphRepository`. `aetheris graph upsert` counts eligible raw
  payloads and exits without aligning or writing anything (T064).
- **Alignment is not on any write path.** `src/graph/align.py` implements the
  as-of join (T060), but nothing outside its tests calls it.
- **`aetheris score` reads only `--text`.** It cannot score ingested payloads,
  and it prints results rather than storing them. A code comment points to
  T065 for this, but T065 is the graph-write telemetry task.
- **The point-in-time replay test is narrower than specified.** See
  [Point-in-time correctness](#point-in-time-correctness).
- **The CLI query grammar differs from its contract.**
  [`cli.md`](.specify/specs/contracts/cli.md) specifies a top-level
  `aetheris query` with `assets --limit N`; the CLI has `aetheris graph query`
  with `--top N`.
- **GitHub Pages is not enabled for the repository.** The deploy workflow
  builds and gates the site, but its publish step has no Pages site to deploy
  to until Pages is switched on with GitHub Actions as the source.

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
