# Quickstart: Validating Aetheris End-to-End

**Purpose**: a runnable validation guide proving the pipeline works. This is a *validation* guide —
implementation code bodies, service internals, and migrations live in `tasks.md` and the
implementation phase, not here.

**Prerequisites**: Python 3.11+, no GPU, no paid API keys, no network access required after setup
(all provider responses replay from cassettes).

---

## 0. Environment

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Provider keys are env-only (constitution: never committed)
export FRED_API_KEY=...        # or leave unset -- dry-run mode works without keys
```

Optional telemetry backends (self-hosted, per `research.md` §C):

```bash
docker run -d --name jaeger -p 16686:16686 -p 4317:4317 jaegertracing/all-in-one:latest
mlflow server --backend-store-uri sqlite:///mlflow.db --default-artifact-root ./mlruns
```

## 1. Verify the constitution gate *before* writing code

```bash
aetheris check-licenses --strict
```
**Expected**: exit `0`, dependency table printed with every license in
`{Apache-2.0, MIT, BSD-3-Clause, public-domain}`.
**Fails if** any transitive dependency is GPL/AGPL/LGPL/SSPL/BUSL — this is the Principle I gate
(exit `3`).

## 2. Unit + contract tests (no network, no quota)

```bash
pytest tests/unit tests/contract -q
```
**Expected**: all pass. Contract tests cover the payload schema rejection rules
(`sentiment-schema.md` §Rejection Rules), the read-only API shape (`graph-api.md`), and the CLI
argument grammar (`cli.md`).

## 3. Layer-decoupling check (Principle II)

```bash
lint-imports
```
**Expected**: exit `0`. Proves `ingestion`, `sentiment`, and `graph` never import `orchestration`
and that `*` never imports `graph` from `sentiment` (see `adapter-protocol.md` §Prohibited
Dependencies).

## 4. Full pipeline dry run (offline, from cassettes)

```bash
aetheris run --trigger schedule --dry-run
```
**Expected**: exit `0`, config + provider quota validation report, **zero live provider calls**.

## 5. Ingestion with cache + rate limiter

```bash
aetheris ingest fred series/observations
aetheris ingest finhub /news
aetheris status
```
**Expected**:
- Raw immutable payload rows appended to SQLite (`aetheris-raw.db`), never mutated on re-run.
- A second identical `ingest` is served from the TTL cache — visible as a cache **hit** in
  `status` (SC-003, ≥80% at steady state).
- An OTel span per fetch, visible at `http://localhost:16686`.

## 6. Sentiment scoring

```bash
aetheris score --since 2026-10-01T00:00:00+00:00 --json > metrics.json
```
**Expected**: one JSON payload per news item, conforming to `sentiment-schema.md`; `polarity_score`
∈ [-1, 1] and `confidence` ∈ [0, 1] with a populated `calibration` block.
**Spot-check**:
```bash
jq '[.[] | select(.polarity_score < -0.6 or .polarity_score > 0.6)] | length' metrics.json
```
**Expected**: a non-zero, sensible count — a run scoring everything near 0.0 means the model loaded
but the mapping is wrong.

## 7. Graph upsert — point-in-time correctness (the critical test)

```bash
aetheris graph upsert --rebuild-edges
```
**Expected**: `INFLUENCES`, `CORRELATES_WITH`, and `TARGETS` edges written with weight ∈ [-1, 1],
confidence ∈ [0, 1].

**Validating VR-5 (no lookahead)** — replay determinism test:

```bash
pytest tests/integration/test_point_in_time.py -q
```
**Expected**: pass. The test asserts that a macro print released *after* a graph timestamp is not
observable by that timestamp, and that re-running the same cassette yields a byte-identical graph
state. **This is the single highest-value validation in the suite** — a leak here invalidates every
downstream query.

**Validating idempotency (VR-6 / FR-013)**:
```bash
aetheris graph upsert --rebuild-edges   # run twice
```
**Expected**: row counts identical, no duplicate `(src, dst, rel_type, valid_from)` keys.

## 8. Read-only query surface

```bash
aetheris query influencers --id equity:NASDAQ:AAPL --top 5 --json
aetheris query sentiment --entity equity:NASDAQ:AAPL --start 2026-10-01T00:00:00+00:00 --json
```
**Expected**: results matching the `graph-api.md` dataclasses, timestamps with explicit UTC offset,
sorted ascending by `timestamp`, `min_confidence` applied before any limit.

## 9. Durability: checkpoint and resume

```bash
# Simulate a mid-run provider failure with a poisoned cassette
aetheris run --trigger schedule          # run fails at the ingestion step
aetheris run --trigger schedule --resume <WORKFLOW_ID>
```
**Expected**:
- Resume starts from the **first failed step**; completed steps are not re-executed (FR-002).
- Elapsed resume time < 60 s (SC-002).
- After `max_attempts=5` with 1s→2s→4s→8s→16s backoff, the step is marked FAILED and its
  compensating action runs (FR-003).

## 10. Evaluation and drift

```bash
aetheris evaluate --since 2026-09-01T00:00:00+00:00
```
**Expected**: an MLflow run logged with confidence distribution, polarity distribution, and Evidently
drift metrics, plus a Prometheus gauge and an OTel span event for the evaluation step (FR-019).

---

## Success Criteria Cross-Reference

| SC | How to verify | Target |
|---|---|---|
| SC-001 P95 run latency | MLflow metrics across N scheduled runs | < 5 min |
| SC-002 Checkpoint resume | §9 above, timed | < 60 s |
| SC-003 Cache hit ratio | `aetheris status` at steady state | ≥ 80% |
| SC-004 FR coverage | `aetheris check-licenses` + pytest coverage report per FR | every FR-001…FR-021 has ≥1 passing test |

## Known Deferred Items

- Multi-hop traversal beyond 2 hops (`graph-api.md` §Limits).
- Redistribution of derived graph data — blocked pending written provider confirmation
  (plan.md V4). Run everything internally until cleared.
- Scale assumption (~500 assets / ~3k news items/day) from `plan.md` Technical Context is
  **unconfirmed**; this quickstart exercises a subset, so it will not catch scale-related
  performance regressions. Add a volume-scale check to `tasks.md` before Milestone 3 closes.
