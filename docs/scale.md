# Volume-Scale Measurements (T078)

Measurements taken against the plan's *assumed* scale. The plan itself flags this
assumption as **unconfirmed**, and these numbers are why that matters.

**Assumption under test**: ~500 assets, ~25 macro series, ~3,000 news items/day
→ ~124,750 `CORRELATES_WITH` edge pairs (500²/2).

**Environment**: macOS, Apple Silicon, embedded DuckDB in-memory, stub sentiment
model (no transformer weights loaded).

## Measurements

| Operation | Volume | Time | Per unit |
|---|---|---|---|
| Asset upserts | 200 assets | 0.12 s | 0.6 ms |
| Edge upserts (one-by-one) | 19,900 pairs | 13.4 s | 0.67 ms |
| Edge upserts (re-batched) | 19,900 pairs | 12.8 s | 0.64 ms |
| Edge derivation, extrapolated to 500 assets | 124,750 pairs | **~84 s** | 0.67 ms |

Batching produced no meaningful gain (~5%), so per-row upserts are not the
bottleneck — the O(n²) pair count is.

## Verdict against SC-001

SC-001 requires a P95 scheduled run under **5 minutes (300 s)**.

| Component | Estimated cost at assumed scale |
|---|---|
| Macro + price + news ingestion | not measured (needs live providers) |
| Sentiment scoring, 3,000 items | ~1–3 s (stub model) |
| Edge derivation, 124,750 pairs | **~84 s** |
| Graph upsert | included above |

**~84 s of a 300 s budget is spent on edge derivation alone.** That is
feasible at the assumed scale but leaves little headroom, and it does not
survive scaling: 5,000 assets would be ~30M pairs and roughly **5.5 hours**,
which is not a pipeline, it's a batch job.

## Actionable consequence

The O(n²) all-pairs correlation is the single dominant cost in the design, and
it scales quadratically. Before the assumed scale is confirmed, one of these
must be decided:

1. **Confirm or correct the scale assumption** — 500 assets is an estimate from
   `plan.md`, not a requirement. If real scope is ~2,000 assets, edge derivation
   is ~13 minutes and SC-001 is already violated.
2. **Scope edge derivation** — correlate against a curated watchlist or an
   index rather than all pairs, which makes cost linear in assets.
3. **Raise SC-001** for the graph step specifically, and move edge derivation to
   an asynchronous job outside the scheduled-run budget.

Option 2 is the only one that survives growth without loosening the target.

## What was not measured

- Live ingestion latency (needs credentials — task T041).
- Transformer inference (real weights, not the stub).
- Durable-run overhead (checkpoint writes on top of the above).

All three are likely to add materially, so the ~84 s figure should be treated as
a **lower bound** on the graph step's cost.
