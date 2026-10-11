"""Unit tests for metric definitions and evaluation (T068) — FR-018, FR-019.

The metric names are part of the contract, not an implementation detail: SC-001,
SC-002, and SC-003 are computed from these series. These tests pin both the
names and the properties that make the evaluation trustworthy — out-of-range
values are counted rather than clamped, and drift degrades to "no data" rather
than a false alarm.
"""

from __future__ import annotations

import pytest

from src.observability.evaluate import (
    Evaluation,
    compute_drift,
    evaluate,
    summarise_scored,
)
from src.observability.metrics import (
    CACHE_HITS,
    CACHE_MISSES,
    DRIFT_SCORE,
    FETCH_ERRORS,
    FETCH_TOTAL,
    GRAPH_UPSERTS,
    MODEL_CONFIDENCE,
    POLARITY_DISTRIBUTION,
    STEP_FAILURES,
    STEP_RETRIES,
    WORKFLOW_DURATION,
    get_metrics,
    reset,
)


@pytest.fixture(autouse=True)
def _reset():
    reset()
    yield
    reset()


def _payload(score: float, confidence: float) -> dict:
    return {"polarity_score": score, "confidence": confidence}


class TestMetricNames:
    """The contract: SC-001..SC-003 are computed from these."""

    @pytest.mark.parametrize(
        "name,expected",
        [
            (WORKFLOW_DURATION, "aetheris.workflow.duration_seconds"),
            (STEP_FAILURES, "aetheris.step.failures_total"),
            (STEP_RETRIES, "aetheris.step.retries_total"),
            (FETCH_TOTAL, "aetheris.ingestion.fetch_total"),
            (FETCH_ERRORS, "aetheris.ingestion.errors_total"),
            (CACHE_HITS, "aetheris.ingestion.cache_hits_total"),
            (CACHE_MISSES, "aetheris.ingestion.cache_misses_total"),
            (GRAPH_UPSERTS, "aetheris.graph.upserts_total"),
            (MODEL_CONFIDENCE, "aetheris.evaluation.confidence"),
            (POLARITY_DISTRIBUTION, "aetheris.evaluation.polarity"),
            (DRIFT_SCORE, "aetheris.evaluation.drift_score"),
        ],
    )
    def test_names_are_stable(self, name, expected):
        """Test that names are stable."""
        assert name == expected

    def test_every_documented_series_exists(self):
        """A series the docs mention must actually be registered."""
        m = get_metrics()
        for attr in (
            "workflow_duration", "step_duration", "step_failures", "step_retries",
            "fetch_total", "fetch_errors", "cache_hits", "cache_misses",
            "graph_upserts", "model_confidence", "polarity_distribution",
            "drift_score",
        ):
            assert getattr(m, attr, None) is not None, f"{attr} is not registered"

    def test_reset_yields_a_fresh_singleton(self):
        """Test that reset yields a fresh singleton."""
        reset()
        first = get_metrics()
        reset()
        assert get_metrics() is not first


class TestSummarise:
    """Test summarise."""
    def test_empty_batch(self):
        """Test that empty batch."""
        summary = summarise_scored([])
        assert summary["items"] == 0
        assert summary["mean_polarity"] == 0.0

    def test_means_are_correct(self):
        """Test that means are correct."""
        summary = summarise_scored(
            [_payload(0.5, 0.9), _payload(-0.5, 0.7)]
        )
        assert summary["items"] == 2
        assert summary["mean_polarity"] == pytest.approx(0.0)
        assert summary["mean_confidence"] == pytest.approx(0.8)

    def test_out_of_range_is_counted_not_clamped(self):
        """Clamping would hide the defect that produced the bad value."""
        summary = summarise_scored([_payload(2.0, 0.9), _payload(0.1, 0.5)])
        assert summary["out_of_range"] == 1
        assert summary["items"] == 1

    def test_min_max_confidence(self):
        """Test that min max confidence."""
        summary = summarise_scored(
            [_payload(0.0, 0.2), _payload(0.0, 0.9), _payload(0.0, 0.5)]
        )
        assert summary["min_confidence"] == pytest.approx(0.2)
        assert summary["max_confidence"] == pytest.approx(0.9)


class TestDrift:
    """Test drift."""
    def test_no_data_is_not_drift(self):
        """Test that no data is not drift."""
        score, detected, notes = compute_drift([], [0.1, 0.2])
        assert detected is False
        assert notes

    def test_identical_distributions_are_not_drift(self):
        """Test that identical distributions are not drift."""
        score, detected, _ = compute_drift([0.1] * 10, [0.1] * 10, threshold=0.05)
        assert detected is False

    def test_large_shift_is_drift(self):
        """Test that large shift is drift."""
        score, detected, notes = compute_drift(
            [0.8] * 10, [0.1] * 10, threshold=0.1
        )
        assert detected is True
        assert score > 0.1
        assert any("moved" in n for n in notes)

    def test_small_sample_warns(self):
        """Test that small sample warns."""
        _, _, notes = compute_drift([0.5, 0.6], [0.1, 0.2])
        assert any("small" in n for n in notes)

    def test_threshold_boundary(self):
        """Test that threshold boundary."""
        _, detected, _ = compute_drift([0.5] * 5, [0.4] * 5, threshold=0.1)
        assert detected is False  # shift is exactly the threshold, not over


class TestEvaluate:
    """Test evaluate."""
    def test_returns_an_evaluation(self):
        """Test that returns an evaluation."""
        result = evaluate([_payload(0.5, 0.9)])
        assert isinstance(result, Evaluation)
        assert result.items_scored == 1
        assert result.mean_polarity == pytest.approx(0.5)

    def test_drift_populated_when_reference_given(self):
        """Test that drift populated when reference given."""
        result = evaluate(
            [_payload(0.8, 0.9)] * 10, reference=[0.1] * 10, drift_threshold=0.1
        )
        assert result.drift_score is not None
        assert result.drift_detected is True

    def test_drift_absent_without_reference(self):
        """Test that drift absent without reference."""
        result = evaluate([_payload(0.1, 0.9)])
        assert result.drift_score is None

    def test_as_dict_is_serialisable(self):
        """Test that as dict is serialisable."""
        import json

        evaluation = evaluate([_payload(0.1, 0.9)])
        json.dumps(evaluation.as_dict())

    def test_evaluation_counts_rejections(self):
        """Test that evaluation counts rejections."""
        result = evaluate([_payload(5.0, 0.9), _payload(0.1, 0.5)])
        assert result.out_of_range_rejections == 1
        assert result.items_scored == 1


class TestMlflowLogging:
    """Test mlflow logging."""
    def test_missing_mlflow_degrades_not_crashes(self):
        """A missing tracker must not break the pipeline."""
        result = evaluate([_payload(0.1, 0.9)])
        from src.observability.evaluate import log_to_mlflow

        # ImportError is caught internally and reported as a status.
        status = log_to_mlflow(result)
        assert isinstance(status, dict)
        assert "logged" in status
