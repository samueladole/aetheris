"""Temperature scaling for calibrated confidence (T048).

Transformer softmax outputs are systematically overconfident — a model can emit
0.98 for text it is only 80% sure about. Passing that through uncalibrated would
violate VR-2, so confidence is scaled by a fitted temperature before it is
exposed.

The scaler is deliberately pure and dependency-free so it can be fitted and
tested without a model, and so the fitted value is reproducible.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from src.sentiment.analyzer import Polarity


def softmax(logits: Sequence[float]) -> list[float]:
    """Numerically stable softmax."""
    if not logits:
        raise ValueError("logits must not be empty")
    peak = max(logits)
    exps = [math.exp(value - peak) for value in logits]
    total = sum(exps)
    return [value / total for value in exps]


@dataclass(frozen=True)
class TemperatureScaler:
    """Divides logits by a temperature before the softmax.

    ``temperature > 1`` flattens the distribution (reduces overconfidence);
    ``temperature < 1`` sharpens it.
    """

    temperature: float

    def __post_init__(self) -> None:
        """Implement the __post_init__ special method."""
        if not math.isfinite(self.temperature) or self.temperature <= 0:
            raise ValueError(
                f"temperature must be a positive, finite number; got {self.temperature}"
            )

    def apply(self, probabilities: Sequence[float]) -> list[float]:
        """Rescale a probability distribution by this temperature.

        Works by inverting to logits, dividing, and re-softmaxing, so the result
        is always a valid distribution.
        """
        if not probabilities:
            raise ValueError("probabilities must not be empty")
        if any(p < 0 for p in probabilities):
            raise ValueError("probabilities must be non-negative")
        total = sum(probabilities)
        if total <= 0:
            raise ValueError("probabilities must sum to a positive value")

        logits = [math.log(p / total) for p in probabilities]
        scaled = [value / self.temperature for value in logits]
        return softmax(scaled)

    def apply_logits(self, logits: Sequence[float]) -> list[float]:
        """Scale raw logits by this temperature."""
        return softmax([value / self.temperature for value in logits])


def calibrate(
    logits_or_probs: Sequence[float],
    *,
    temperature: float = 1.0,
) -> Polarity:
    """Calibrate a model output and return a signed polarity plus confidence.

    Accepts either logits or probabilities. Polarity is computed from the
    calibrated distribution, so the reported confidence is never the raw
    overconfident softmax maximum.
    """
    from src.sentiment.analyzer import score_from_probs

    # Distinguish logits from probabilities: probabilities are non-negative and
    # sum to ~1. A log-scaled probability vector satisfies both, so the caller
    # must pass `from_logits` when the values are actually logits.
    values = list(logits_or_probs)
    if any(v < 0 for v in values) or abs(sum(values) - 1.0) > 1e-6:
        probabilities = softmax(values)
    else:
        probabilities = list(values)

    scaled = TemperatureScaler(temperature=temperature).apply(probabilities)
    return score_from_probs(*scaled)


def fit_temperature(
    samples: Sequence[tuple[Sequence[float], Sequence[float]]],
    *,
    lower: float = 0.05,
    upper: float = 10.0,
    iterations: int = 60,
) -> float:
    """Fit a temperature from ``(base, target)`` pairs by minimising NLL.

    ``base`` is a model's raw output distribution and ``target`` the observed
    calibrated distribution for the same item. The fitted temperature is the one
    whose ``apply`` reproduces the targets best.

    Fitting against a *target* rather than against the samples themselves matters:
    applying a temperature to an already-scaled distribution round-trips through
    normalisation and would trivially return 1.0. Golden-section search keeps
    the fit bounded and deterministic, so the value is reproducible.
    """
    if not samples:
        raise ValueError("samples must not be empty")

    pairs = [(list(b), list(t)) for b, t in samples if b and t]
    if not pairs:
        raise ValueError("no usable (base, target) pairs")

    def nll(temperature: float) -> float:
        """Cross-entropy between the produced distribution and the target.

        Cross-entropy — not the produced argmax's own negative log-likelihood —
        is the right objective. Maximising the produced confidence instead would
        push temperature to zero, which is the opposite of fitting.
        """
        scaler = TemperatureScaler(temperature=temperature)
        total = 0.0
        for base, target in pairs:
            produced = scaler.apply(base)
            target_total = sum(target)
            for i, probability in enumerate(target):
                if probability > 0:
                    total -= (probability / target_total) * math.log(
                        max(produced[i], 1e-12)
                    )
        return total / len(pairs)

    ratio = (math.sqrt(5) - 1) / 2
    lo, hi = lower, upper
    c = hi - ratio * (hi - lo)
    d = lo + ratio * (hi - lo)
    for _ in range(iterations):
        if nll(c) < nll(d):
            hi = d
        else:
            lo = c
        c = hi - ratio * (hi - lo)
        d = lo + ratio * (hi - lo)
        if hi - lo < 1e-6:
            break
    return round((lo + hi) / 2, 4)


__all__ = [
    "TemperatureScaler",
    "calibrate",
    "fit_temperature",
    "softmax",
]
