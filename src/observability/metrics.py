"""Pipeline metric definitions (FR-018).

Constitution §IV and spec §3.5 require tracking system latency, error rates,
cache hit/miss ratios, and queue depths. Success criteria SC-001, SC-002, and
SC-003 are computed from the series defined here — which is why the metric
names are part of the contract, not an implementation detail.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.observability.telemetry import get_meter

# ---------------------------------------------------------------------------
# Canonical metric names — referenced by quickstart.md and SC-001..SC-003
# ---------------------------------------------------------------------------
WORKFLOW_DURATION = "aetheris.workflow.duration_seconds"
STEP_DURATION = "aetheris.step.duration_seconds"
STEP_RETRIES = "aetheris.step.retries_total"
STEP_FAILURES = "aetheris.step.failures_total"
STEP_COMPENSATIONS = "aetheris.step.compensations_total"
WORKFLOW_RESUME = "aetheris.workflow.resume_seconds"

FETCH_TOTAL = "aetheris.ingestion.fetch_total"
FETCH_ERRORS = "aetheris.ingestion.errors_total"
FETCH_FALLBACKS = "aetheris.ingestion.fallbacks_total"
CACHE_HITS = "aetheris.ingestion.cache_hits_total"
CACHE_MISSES = "aetheris.ingestion.cache_misses_total"
QUOTA_REMAINING = "aetheris.ingestion.quota_remaining"
QUEUE_DEPTH = "aetheris.ingestion.queue_depth"

INFERENCE_LATENCY = "aetheris.sentiment.inference_seconds"
INFERENCE_ITEMS = "aetheris.sentiment.items_total"
SCORES_OUT_OF_RANGE = "aetheris.sentiment.out_of_range_total"

GRAPH_UPSERTS = "aetheris.graph.upserts_total"
GRAPH_ALIGNMENT_LEAKS = "aetheris.graph.alignment_leaks_total"
DRIFT_SCORE = "aetheris.evaluation.drift_score"
MODEL_CONFIDENCE = "aetheris.evaluation.confidence"
POLARITY_DISTRIBUTION = "aetheris.evaluation.polarity"


# Histogram buckets tuned for a batch pipeline (seconds), not a web service.
_LATENCY_BUCKETS = (0.05, 0.25, 1.0, 2.5, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0)

_UNIT_COUNT = "1"
_UNIT_SECONDS = "s"


@dataclass(frozen=True)
class _Metrics:
    """Holds every instrument the pipeline records to.

    Typed as ``Any`` deliberately: the OTel SDK returns opaque instrument
    objects whose only useful contract is the recording methods it exposes.
    """

    # orchestration
    workflow_duration: Any
    step_duration: Any
    step_retries: Any
    step_failures: Any
    step_compensations: Any
    workflow_resume: Any
    # ingestion
    fetch_total: Any
    fetch_errors: Any
    fetch_fallbacks: Any
    cache_hits: Any
    cache_misses: Any
    quota_remaining: Any
    queue_depth: Any
    # sentiment
    inference_latency: Any
    inference_items: Any
    scores_out_of_range: Any
    # graph
    graph_upserts: Any
    graph_alignment_leaks: Any
    # evaluation
    drift_score: Any
    model_confidence: Any
    polarity_distribution: Any


_singleton: _Metrics | None = None


def get_metrics() -> _Metrics:
    """Return (creating on first use) the process-wide metric instruments."""
    global _singleton
    if _singleton is not None:
        return _singleton

    m = get_meter()

    def hist(name: str, desc: str, unit: str = _UNIT_SECONDS) -> Any:
        # OTel's create_histogram only accepts (name, unit, description) plus
        # advisory bucket boundaries — buckets must not be passed positionally.
        return m.create_histogram(
            name=name,
            unit=unit,
            description=desc,
            explicit_bucket_boundaries_advisory=list(_LATENCY_BUCKETS),
        )

    _singleton = _Metrics(
        # orchestration
        workflow_duration=hist(
            WORKFLOW_DURATION, "Full workflow duration"
        ),
        step_duration=hist(STEP_DURATION, "Individual step duration"),
        workflow_resume=hist(
            WORKFLOW_RESUME, "Checkpoint resume duration"
        ),
        inference_latency=hist(
            INFERENCE_LATENCY, "Sentiment inference latency"
        ),
        # sentiment distributions (unitless scores)
        model_confidence=hist(
            MODEL_CONFIDENCE, "Calibrated confidence", _UNIT_COUNT
        ),
        polarity_distribution=hist(
            POLARITY_DISTRIBUTION, "Polarity score distribution", _UNIT_COUNT
        ),
        # counters
        step_retries=m.create_counter(
            STEP_RETRIES, _UNIT_COUNT, "Step retry attempts"
        ),
        step_failures=m.create_counter(
            STEP_FAILURES, _UNIT_COUNT, "Terminal step failures"
        ),
        step_compensations=m.create_counter(
            STEP_COMPENSATIONS, _UNIT_COUNT, "Compensating actions executed"
        ),
        fetch_total=m.create_counter(
            FETCH_TOTAL, _UNIT_COUNT, "Provider fetch attempts"
        ),
        fetch_errors=m.create_counter(
            FETCH_ERRORS, _UNIT_COUNT, "Provider fetch errors"
        ),
        fetch_fallbacks=m.create_counter(
            FETCH_FALLBACKS, _UNIT_COUNT, "Provider fallbacks taken"
        ),
        cache_hits=m.create_counter(CACHE_HITS, _UNIT_COUNT, "Cache hits"),
        cache_misses=m.create_counter(CACHE_MISSES, _UNIT_COUNT, "Cache misses"),
        queue_depth=m.create_up_down_counter(
            QUEUE_DEPTH, _UNIT_COUNT, "Pending queue depth"
        ),
        quota_remaining=m.create_up_down_counter(
            QUOTA_REMAINING, _UNIT_COUNT, "Remaining free-tier quota"
        ),
        inference_items=m.create_counter(
            INFERENCE_ITEMS, _UNIT_COUNT, "Sentiment items scored"
        ),
        scores_out_of_range=m.create_counter(
            SCORES_OUT_OF_RANGE,
            _UNIT_COUNT,
            "Scores rejected for being out of range",
        ),
        graph_upserts=m.create_counter(
            GRAPH_UPSERTS, _UNIT_COUNT, "Graph upsert operations"
        ),
        graph_alignment_leaks=m.create_counter(
            GRAPH_ALIGNMENT_LEAKS,
            _UNIT_COUNT,
            "Detected no-lookahead violations",
        ),
        # gauges
        drift_score=m.create_gauge(DRIFT_SCORE, _UNIT_COUNT, "Latest data drift score"),
    )
    return _singleton


def reset() -> None:
    """Reset the metric singleton (used by tests to isolate metric names)."""
    global _singleton
    _singleton = None
