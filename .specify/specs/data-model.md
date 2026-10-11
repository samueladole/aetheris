# Data Model: Aetheris

**Date**: 2026-10-10 | **Spec**: [`./spec.md`](./spec.md) | **Plan**: [`./plan.md`](./plan.md)
**Research**: [`./research.md`](./research.md)

Store: **DuckDB (MIT)**, embedded, single-file. Nodes and edges are typed tables; all mutations are
idempotent `INSERT … ON CONFLICT` upserts. This file defines identity, validation, and derivation
rules — not implementation code.

---

## 1. Node Entities

### 1.1 `Asset`
Represents a tradable instrument.

| Field | Type | Rule |
|---|---|---|
| `asset_id` | `VARCHAR` (PK) | Surrogate key, `asset_class:exchange:symbol` — e.g. `equity:NASDAQ:AAPL` |
| `ticker` | `VARCHAR` | Native exchange symbol, e.g. `AAPL` |
| `name` | `VARCHAR` | Display name |
| `asset_class` | `VARCHAR` | Enum: `equity`, `forex`, `crypto`, `index` |
| `exchange` | `VARCHAR` | MIC or provider venue code |
| `currency` | `VARCHAR(3)` | ISO 4217 |
| `is_active` | `BOOLEAN` | `TRUE` unless delisted/paused |
| `updated_at` | `TIMESTAMP` | UTC |

**Uniqueness**: `asset_id` is the sole identity key. Ticker alone is *not* unique
(`BF.B` vs `BF-B`; the same ticker across venues).
**Closure rule (Outstanding from clarify)**: a new `asset_class` value requires a spec amendment,
since §3.4 fixes the four-value enum.

### 1.2 `MacroIndicator`
Represents a macroeconomic series.

| Field | Type | Rule |
|---|---|---|
| `indicator_code` | `VARCHAR` (PK) | Provider canonical code, e.g. `FRED:GDP`, `FRED:CPIAUCSL` |
| `name` | `VARCHAR` | Human-readable series name |
| `unit` | `VARCHAR` | e.g. `USD`, `percent`, `index` |
| `frequency` | `VARCHAR` | Enum: `daily`, `weekly`, `monthly`, `quarterly`, `annual` |
| `seasonal_adjustment` | `VARCHAR` | e.g. `SA`, `NSA` |
| `provider` | `VARCHAR` | From §3.2.1 registry |
| `updated_at` | `TIMESTAMP` | UTC |

**Uniqueness**: `indicator_code` (provider-prefixed). This is what lets FRED and a second macro
source coexist without collision.

### 1.3 `SentimentMetric`
Represents one scored text item. Highest-volume node type (~50k–250k/day at planned scale).

| Field | Type | Rule |
|---|---|---|
| `metric_id` | `VARCHAR` (PK) | `${entity_id}:${source_reference}:${text_hash}:${model_id}@${revision}` |
| `entity_id` | `VARCHAR` | Resolves to an `Asset.asset_id` or `MacroIndicator.indicator_code` |
| `score` | `DOUBLE` | **Constraint `BETWEEN -1.0 AND 1.0`** — signed polarity |
| `confidence` | `DOUBLE` | **Constraint `BETWEEN 0.0 AND 1.0`** — calibrated (temperature-scaled) |
| `timestamp` | `TIMESTAMP` | UTC; **the observation time of the text, not the inference time** |
| `source_reference` | `VARCHAR` | Stable ingested-news identifier (provider + article id) |
| `model_id` | `VARCHAR` | Pinned model checkpoint id + hash, for lineage (FR-019) |
| `text_hash` | `VARCHAR` | SHA-256 of normalized text; dedupes republished items |
| `created_at` | `TIMESTAMP` | UTC ingest time |

**Uniqueness**: `metric_id`. Re-scoring the same text with a different model produces a *distinct*
row, preserving lineage rather than overwriting history — this is the "data harmonization across
disparate frequencies" requirement of FR-013.

The model's id and revision are part of `metric_id` precisely to make that true: two models scoring
identical text produce different scores, and without the model in the identity the second write
would silently overwrite the first.

**Validation**: `score`/`confidence` bounds enforced by DB constraints *and* by
`src/sentiment/schema.py` before write; uncalibrated softmax output must never reach the graph.

---

## 2. Edge Entities

All edges share this shape (§3.4 + clarification Q2):

| Field | Type | Rule |
|---|---|---|
| `src_node` | `VARCHAR` | Must exist in one of the three node tables |
| `dst_node` | `VARCHAR` | Must exist in one of the three node tables |
| `rel_type` | `VARCHAR` | Enum: `INFLUENCES`, `CORRELATES_WITH`, `TARGETS` |
| `weight` | `DOUBLE` | **Constraint `BETWEEN -1.0 AND 1.0`, signed** (clarification Q2/B) |
| `confidence` | `DOUBLE` | **Constraint `BETWEEN 0.0 AND 1.0`** — statistical significance + observation count |
| `valid_from` | `TIMESTAMP` | Start of the observation window this edge summarizes |
| `valid_to` | `TIMESTAMP` | End of window; `NULL` = currently open |
| `observation_count` | `INTEGER` | Sample size backing `confidence` |
| `computed_at` | `TIMESTAMP` | UTC |

**Primary key**: `(src_node, dst_node, rel_type, valid_from)` — this is what makes re-running the
edge derivation for the same window idempotent (FR-013).

### 2.1 `INFLUENCES`
Directed: `MacroIndicator` → `Asset` (or `MacroIndicator` → `MacroIndicator`).
- `weight`: signed influence strength. Negative values are semantically meaningful (an inverse
  relationship, e.g. rising rates depressing an equity).
- `confidence`: from statistical significance of the fitted influence over the window.

### 2.2 `CORRELATES_WITH`
Undirected semantically, stored as two directed rows (`A→B` and `B→A`) or one with a canonical
ordering — **canonical ordering (`src_node < dst_node`)** is chosen to prevent duplicate pairs.
- `weight`: Pearson correlation coefficient over the observation window, `r ∈ [-1, 1]`.
- `confidence`: from significance test and `observation_count`.

### 2.3 `TARGETS`
Directed: `SentimentMetric` → `Asset` (or → `MacroIndicator`). This is the edge that attaches each
scored text to the entity it describes.
- `weight`: strength of the sentiment-to-entity link (co-mention strength / entity-resolution
  confidence).
- Provenance: paired with `SentimentMetric.source_reference` so a consumer can always trace an edge
  back to the article that produced it.

---

## 3. Point-in-Time Correctness (the binding invariant)

**Rule (clarification Q1/A, spec §3.4 + §4):** all alignment across disparate frequencies uses a
**backward as-of join with a strict no-lookahead invariant** — a data point may only observe values
already published at its own timestamp.

Consequences encoded in the schema:
- Every observable value carries an **availability timestamp** (release/publication time), which is
  distinct from the **observation timestamp** (the period the value describes).
- `MacroIndicator` observations must store both: a CPI print released on the 12th for January data
  is *not* available to a graph state on January 31. This is the single most common way a financial
  pipeline leaks future information, and it is why the field pair is mandatory rather than optional.
- Acceptance test (FR-014): a fixed aligned output must be reproducible from a fixed input
  cassette — same inputs → same graph state, byte-identical.

---

## 4. Raw Immutable Payloads (not in the graph)

`RawPayload` lives in **SQLite**, append-only, and is never mutated in place.

| Field | Rule |
|---|---|
| `payload_id` | `${provider}:${endpoint}:${request_hash}:${fetched_at}` |
| `provider`, `endpoint`, `request_hash` | From the §3.2.1 registry |
| `schema_version` | Payload schema version (FR-015) |
| `body` | Raw response bytes, stored verbatim |
| `fetched_at` | UTC |
| `ttl_seconds` | From registry; drives the caching layer (FR-007) |

Immutability is what makes `vcrpy` cassettes, replay-based integration tests, and post-hoc
recomputation all possible without re-hitting free-tier quotas.

---

## 5. State Transitions

### Workflow step (orchestration)
`PENDING → RUNNING → SUCCEEDED | FAILED → COMPENSATING → COMPENSATED`
- `FAILED` is terminal only after bounded retries are exhausted (`base_delay_ms=1000`,
  `multiplier=2`, `max_attempts=5`, jitter) — FR-003.
- `COMPENSATING → COMPENSATED` is the failure-compensation path required by constitution §III.

### SentimentMetric (derived, append-only)
`INGESTED → SCORED → CALIBRATED → ALIGNED → UPSERTED`
Never transitions backwards. Re-scoring with a new model creates a new row (see `metric_id`).

### Provider adapter
`HEALTHY → RATE_LIMITED → EXHAUSTED → (cooldown) → HEALTHY`
`RATE_LIMITED` (429) and `EXHAUSTED` (quota spent) drive the fallback order in the §3.2.1 registry
(FR-020); the failure is recorded as a trace span.

---

## 6. Validation Rules Summary

| ID | Rule | Enforced by |
|---|---|---|
| VR-1 | `SentimentMetric.score ∈ [-1.0, 1.0]` | DB constraint + `sentiment/schema.py` |
| VR-2 | `SentimentMetric.confidence ∈ [0.0, 1.0]`, calibrated only | DB constraint + calibration module |
| VR-3 | `Edge.weight ∈ [-1.0, 1.0]`, signed | DB constraint |
| VR-4 | `Edge.confidence ∈ [0.0, 1.0]` | DB constraint |
| VR-5 | No data point reads a value published after its own timestamp | as-of join + FR-014 replay test |
| VR-6 | Upserts are idempotent for identical `(key, window)` | PK design + FR-013 tests |
| VR-7 | Every `SentimentMetric` links to a `RawPayload` and a `model_id` | FK-ish check at write time |
| VR-8 | `RawPayload` rows are append-only | Schema: no `UPDATE`/`DELETE` grants in code |
| VR-9 | Model checkpoints must be permissively licensed | `src/sentiment/models.py` manifest (V3) |
