# Contract: CLI (FR-021)

**Module**: `src/cli/main.py` · **Runtime**: `aetheris <command> [options]`
**Status**: v1 — read-only commands plus pipeline operation commands. No network service.

**Global options** (all commands):

| Flag | Meaning |
|---|---|
| `--config PATH` | Settings file; defaults to `.specify/config/aetheris.toml` then env vars |
| `--db PATH` | DuckDB graph file; default `./aetheris.duckdb` |
| `--raw PATH` | SQLite raw-payload file; default `./aetheris-raw.db` |
| `--json` | Emit machine-readable JSON on stdout (default: human table) |
| `--trace` | Force an OTel span export for this invocation |

**Exit codes**: `0` success · `1` runtime failure · `2` usage error · `3` constitution/license check failure.

---

## Commands

### `aetheris run`
Execute the full pipeline workflow durably (FR-001, FR-016).
```
aetheris run [--trigger schedule|event] [--dry-run] [--resume WORKFLOW_ID]
```
- `--trigger schedule|event` selects the §4 trigger path.
- `--resume WORKFLOW_ID` resumes a previously checkpointed run from the first failed step (FR-002).
- `--dry-run` validates configuration, provider credentials, and quotas **without** issuing any
  live provider call. This is the CI smoke test.

### `aetheris ingest`
Run a single ingestion adapter directly (FR-004 … FR-008, FR-015).
```
aetheris ingest <provider> <endpoint> [--force] [--ttl SECONDS]
```
- `--force` bypasses the TTL cache (FR-007); otherwise a cached payload is served.
- Writes a raw immutable payload row (VR-8) and emits an OTel span.

### `aetheris score`
Score previously-ingested news text (FR-009, FR-010).
```
aetheris score [--entity ENTITY_ID] [--since ISO8601] [--model MODEL_ID]
```
- `--model` must appear in the compliant model manifest
  (`src/sentiment/models.py`); otherwise exit code `3` (licensing gate).
- Emits one `SentimentMetric` JSON payload per item, conforming to
  [`sentiment-schema.md`](./sentiment-schema.md).

### `aetheris graph upsert`
Align and upsert into the graph (FR-013, FR-014).
```
aetheris graph upsert [--since ISO8601] [--until ISO8601] [--rebuild-edges]
```
- Applies the backward as-of join with the no-lookahead invariant.
- `--rebuild-edges` recomputes `INFLUENCES`/`CORRELATES_WITH` for the window and replaces rows for
  the same `(src, dst, rel_type, valid_from)` key — idempotent by PK.

### `aetheris query`
Read-only graph queries (FR-021).
```
aetheris query entity       --id NODE_ID
aetheris query sentiment   --entity ENTITY_ID [--start ISO8601] [--end ISO8601]
                           [--min-confidence FLOAT] [--limit N]
aetheris query edges       --from NODE_ID [--rel REL_TYPE] [--min-confidence FLOAT]
aetheris query influencers --id NODE_ID [--top N] [--min-confidence FLOAT]
aetheris query assets     [--class equity|forex|crypto|index] [--limit N]
```
- Output mirrors `src/graph/api.py` exactly; the CLI is a thin projection, never an alternative
  implementation.
- `--json` yields a JSON array of the dataclasses in [`graph-api.md`](./graph-api.md).

### `aetheris evaluate`
Run the evaluation/drift step and log to MLflow (FR-019).
```
aetheris evaluate [--since ISO8601] [--min-confidence FLOAT]
```
- Logs model outputs, confidence distributions, and Evidently drift metrics for the window.

### `aetheris status`
Report provider health, quota consumption, cache hit ratio, and last workflow state.
```
aetheris status [--verbose]
```
- Surfaces the `HEALTHY → RATE_LIMITED → EXHAUSTED → HEALTHY` adapter state machine
  (data-model.md §5) and cache hit/miss against SC-003.

### `aetheris check-licenses`
Constitution Principle I gate.
```
aetheris check-licenses [--strict]
```
- Walks the installed dependency tree plus the model manifest and fails with exit code `3` on any
  non-permissive license. Wired into CI so Principle I cannot regress silently.

---

## Output Conventions

- Timestamps: ISO-8601 with explicit UTC offset (`2026-10-10T14:30:00+00:00`). Never naive.
- Floats: `score` and `confidence` rendered with 4 decimal places in human output, full precision in
  `--json`.
- Empty results are `[]` in JSON and `(no results)` in human output — never an error.
