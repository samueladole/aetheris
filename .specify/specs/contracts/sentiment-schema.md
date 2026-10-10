# Contract: Sentiment Output Payload (FR-010)

**Module**: `src/sentiment/schema.py`
**Producer**: `src/sentiment/analyzer.py` · **Consumer**: `src/graph/repository.py`

This is the wire contract between the Sentiment Engine and the Knowledge Graph Engine. It is the
only payload shape the graph layer is permitted to accept.

---

## JSON Schema

```json
{
  "schema_version": "1.0",
  "metric_id": "equity:NASDAQ:AAPL:news:finnhub:abc123:9f86d081884c7d65",
  "entity_id": "equity:NASDAQ:AAPL",
  "polarity_score": -0.42,
  "confidence": 0.87,
  "timestamp": "2026-10-10T14:30:00+00:00",
  "source_reference": "finnhub:news:abc123",
  "model": {
    "id": "AnkitAI/FinSense-ModernBERT-Financial-News-Sentiment-Analysis",
    "revision": "<pinned git sha / hash>",
    "license": "Apache-2.0"
  },
  "calibration": {
    "method": "temperature_scaling",
    "temperature": 1.73,
    "calibrated_on": "financial_phrasebank:sentences_allagree:holdout"
  },
  "provenance": {
    "payload_id": "finnhub:/news:<request_hash>:2026-10-10T14:29:58+00:00",
    "text_hash": "<sha256 of normalized text>",
    "text_length_chars": 812
  }
}
```

## Field Contracts

| Field | Type | Required | Rule |
|---|---|---|---|
| `schema_version` | string | yes | `"1.0"` for v1; bump requires a spec amendment |
| `metric_id` | string | yes | `${entity_id}:${source_reference}:${text_hash}` — the node PK (data-model.md §1.3) |
| `entity_id` | string | yes | Resolves to an `Asset.asset_id` or `MacroIndicator.indicator_code` |
| `polarity_score` | number | yes | **`[-1.0, 1.0]`**, `P(pos) − P(neg)`. Must not be an integer `0` from a failed parse |
| `confidence` | number | yes | **`[0.0, 1.0]`**, temperature-scaled softmax max |
| `timestamp` | string | yes | ISO-8601 **with UTC offset**; observation time of the text, not inference time |
| `source_reference` | string | yes | Provider + article id; enables trace-back from graph edge to article |
| `model.id` / `model.revision` / `model.license` | object | yes | Lineage (FR-019). `license` must be `Apache-2.0`, `MIT`, or `BSD-3-Clause` |
| `calibration.method` / `temperature` / `calibrated_on` | object | yes | Confidence provenance |
| `provenance.payload_id` | string | yes | Links to the immutable `RawPayload` row (VR-7) |
| `provenance.text_hash` | string | yes | SHA-256 of normalized text; dedupes republished items |
| `provenance.text_length_chars` | integer | yes | Sanity bound; >8192 is rejected (model context limit) |

## Rejection Rules (fail loudly, never write a partial node)

The analyzer must reject — raising `ValidationError`, not silently clamping — when:

1. `polarity_score` outside `[-1.0, 1.0]`
2. `confidence` outside `[0.0, 1.0]`, or uncalibrated (raw softmax max with no `calibration` block)
3. `timestamp` is timezone-naive (VR-5 cannot be enforced without it)
4. `text_length_chars > 8192` — exceeds the primary model's context window; must be chunked upstream,
   never silently truncated
5. `model.license` is not `Apache-2.0` / `MIT` / `BSD-3-Clause` — **Principle I gate**
6. `entity_id` does not resolve to a known node — the graph layer must never invent an `Asset`
7. Duplicate `metric_id` with an identical `model.revision` — an idempotent no-op, not an error

## Non-Goals

- Raw article text is **not** stored in the payload or the graph — only `text_hash` and
  `source_reference`. Article bodies stay in the immutable `RawPayload` store.
- No sentiment aggregation (daily means, trend lines). That is a consumer concern; the graph stores
  per-item points.
