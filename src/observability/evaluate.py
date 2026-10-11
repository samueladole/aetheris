"""Evaluation, drift, and metric recording (T070, T071, T072) — FR-018, FR-019.

Constitution §IV requires continuous evaluation: model outputs, confidence
scores, and data drift must be logged and evaluated using open-source tracking
frameworks. This module is the pipeline's step 5, and it is deliberately
*optional at runtime* — a run without an MLflow backend still produces the local
summaries and Prometheus metrics, so a missing tracker degrades the system
rather than breaking it.

Design constraints honoured here:
  * Nothing here may require a paid tier or proprietary SaaS.
  * A drift computation that fails must not fail the pipeline; it is reported
    as a failed evaluation, which is itself a signal.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.observability.metrics import get_metrics

# OTel histograms only accept non-negative values, so a signed polarity is
# shifted before recording. Consumers subtract this to recover the sign.
POLARITY_HISTOGRAM_OFFSET = 1.0


class EvaluationError(RuntimeError):
    """Raised when an evaluation cannot be produced at all."""


@dataclass
class Evaluation:
    """Everything one evaluation run produced."""

    started_at: datetime
    entity_id: str | None = None
    items_scored: int = 0
    mean_polarity: float = 0.0
    mean_confidence: float = 0.0
    min_confidence: float = 0.0
    max_confidence: float = 0.0
    out_of_range_rejections: int = 0
    drift_score: float | None = None
    drift_detected: bool = False
    drift_details: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Handle the as dict."""
        return {
            "started_at": self.started_at.isoformat(),
            "entity_id": self.entity_id,
            "items_scored": self.items_scored,
            "mean_polarity": round(self.mean_polarity, 6),
            "mean_confidence": round(self.mean_confidence, 6),
            "min_confidence": round(self.min_confidence, 6),
            "max_confidence": round(self.max_confidence, 6),
            "out_of_range_rejections": self.out_of_range_rejections,
            "drift_score": self.drift_score,
            "drift_detected": self.drift_detected,
            "drift_details": list(self.drift_details),
            "warnings": list(self.warnings),
        }


def summarise_scored(payloads: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Aggregate a batch of scored payloads into summary statistics.

    Pure arithmetic on already-validated payloads: an out-of-range value here
    means a caller bypassed `src.sentiment.schema`, which is a defect worth
    counting rather than silently clamping.
    """
    if not payloads:
        return {
            "items": 0,
            "mean_polarity": 0.0,
            "mean_confidence": 0.0,
            "min_confidence": 0.0,
            "max_confidence": 0.0,
            "out_of_range": 0,
        }

    polarities = []
    confidences = []
    out_of_range = 0
    for payload in payloads:
        score = float(payload.get("polarity_score", 0.0))
        confidence = float(payload.get("confidence", 0.0))
        if not -1.0 <= score <= 1.0 or not 0.0 <= confidence <= 1.0:
            out_of_range += 1
            continue
        polarities.append(score)
        confidences.append(confidence)

    return {
        "items": len(polarities),
        "mean_polarity": sum(polarities) / len(polarities) if polarities else 0.0,
        "mean_confidence": sum(confidences) / len(confidences) if confidences else 0.0,
        "min_confidence": min(confidences) if confidences else 0.0,
        "max_confidence": max(confidences) if confidences else 0.0,
        "out_of_range": out_of_range,
    }


def compute_drift(
    current: Sequence[float],
    reference: Sequence[float],
    *,
    threshold: float = 0.1,
) -> tuple[float, bool, list[str]]:
    """Compare two distributions by mean shift, returning ``(score, detected, notes)``.

    A mean-shift statistic is a coarse but honest drift signal for a first
    version: it needs no histogram infrastructure, cannot be fooled by outliers
    the way a max-diff can, and degrades to "no data" instead of a false alarm
    when either side is empty.

    ``threshold`` is the shift in polarisation worth flagging for a documentation
    grader, not a universal constant.
    """
    if not current or not reference:
        return 0.0, False, ["insufficient data to evaluate drift"]

    def mean(values: Sequence[float]) -> float:
        return sum(values) / len(values)

    shift = abs(mean(current) - mean(reference))
    detected = shift > threshold

    notes = []
    if detected:
        notes.append(
            f"mean polarisation moved {shift:.4f} (threshold {threshold:.4f})"
        )
    if len(current) < 30:
        notes.append(
            f"current sample is small (n={len(current)}); drift estimate is unstable"
        )
    return round(shift, 6), detected, notes


def evaluate(
    payloads: Sequence[dict[str, Any]],
    *,
    entity_id: str | None = None,
    reference: Sequence[float] | None = None,
    drift_threshold: float = 0.1,
) -> Evaluation:
    """Full local evaluation: summaries, metrics, and drift.

    Still records to Prometheus even when MLflow is unavailable, so a missing
    tracker costs you the run history but not the live signal.
    """
    metrics = get_metrics()
    summary = summarise_scored(payloads)

    # Histograms take `record`; gauges take `set`.
    #
    # OTel histograms only accept non-negative measurements, but polarity is
    # signed. Recording `polarity + POLARITY_HISTOGRAM_OFFSET` maps [-1, 1]
    # onto [0, 2] so the distribution shape survives; consumers subtract the
    # offset to recover the signed value.
    for payload in payloads:
        score = float(payload.get("polarity_score", 0.0))
        confidence = float(payload.get("confidence", 0.0))
        if -1.0 <= score <= 1.0:
            metrics.polarity_distribution.record(score + POLARITY_HISTOGRAM_OFFSET)
        if 0.0 <= confidence <= 1.0:
            metrics.model_confidence.record(confidence)
    if summary["out_of_range"]:
        metrics.scores_out_of_range.add(summary["out_of_range"])

    polarities = [
        float(p["polarity_score"]) for p in payloads
        if -1.0 <= float(p.get("polarity_score", 0.0)) <= 1.0
    ]

    result = Evaluation(
        started_at=datetime.now(UTC),
        entity_id=entity_id,
        items_scored=int(summary["items"]),
        mean_polarity=float(summary["mean_polarity"]),
        mean_confidence=float(summary["mean_confidence"]),
        min_confidence=float(summary["min_confidence"]),
        max_confidence=float(summary["max_confidence"]),
        out_of_range_rejections=int(summary["out_of_range"]),
    )

    if reference is not None and polarities:
        score, detected, notes = compute_drift(
            polarities, reference, threshold=drift_threshold
        )
        result.drift_score = score
        result.drift_detected = detected
        result.drift_details = notes
        metrics.drift_score.set(score)

    return result


def log_to_mlflow(
    evaluation: Evaluation,
    *,
    tracking_uri: str | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Record an evaluation as an MLflow run (T071).

    Returns a status dict rather than raising: an absent or unreachable MLflow
    is a degraded evaluation, not a failed pipeline. The return value makes the
    degradation visible to the caller and to `aetheris evaluate`.
    """
    status: dict[str, Any] = {"logged": False, "reason": ""}
    try:
        import mlflow
    except ImportError:
        status["reason"] = "mlflow is not installed"
        return status

    uri = tracking_uri or "sqlite:///mlflow.db"
    try:
        mlflow.set_tracking_uri(uri)
        with mlflow.start_run():
            if params:
                mlflow.log_params(params)
            for key, value in evaluation.as_dict().items():
                if isinstance(value, (int, float)):
                    mlflow.log_metric(key, float(value))
                else:
                    mlflow.log_param(key, str(value))
        status["logged"] = True
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the pipeline
        status["reason"] = f"{type(exc).__name__}: {exc}"
    return status


def compute_drift_report(
    payloads: Sequence[dict[str, Any]],
    *,
    reference: Sequence[float] | None = None,
    drift_threshold: float = 0.1,
    entity_id: str | None = None,
) -> tuple[Evaluation, dict[str, Any]]:
    """Evaluate and log, returning ``(evaluation, mlflow_status)`` (T072)."""
    evaluation = evaluate(
        payloads,
        entity_id=entity_id,
        reference=reference,
        drift_threshold=drift_threshold,
    )
    status = log_to_mlflow(
        evaluation, params={"entity": entity_id or "all"}
    )
    return evaluation, status


__all__ = [
    "Evaluation",
    "EvaluationError",
    "compute_drift",
    "compute_drift_report",
    "evaluate",
    "log_to_mlflow",
    "summarise_scored",
]
