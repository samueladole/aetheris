"""Sentiment analyzer: polarity and confidence from model outputs (T047).

The mapping is defined in `.specify/specs/contracts/sentiment-schema.md` and
pinned by `tests/unit/test_analyzer.py`:

  * ``polarity  = P(positive) - P(negative)``  — signed, in [-1, 1]
  * ``confidence = max(P(pos), P(neg), P(neu))`` — before calibration

Both are computed from **calibrated** probabilities by the caller
(`src.sentiment.calibration`), because a raw transformer softmax is
systematically overconfident and an uncalibrated maximum would violate VR-2.

This module holds the arithmetic and a stub-model backend only. Loading real
weights is `score_text`, which is deliberately the sole place transformers is
imported, so the module remains importable — and testable — without torch or a
multi-hundred-megabyte download.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from src.sentiment.models import ModelSpec
from src.sentiment.models import get as get_model


class Polarity(float):
    """A scored item: signed polarity **as a float**, plus calibrated confidence.

    Subclassing ``float`` is deliberate. A polarity is a number, so it should
    behave like one — directly comparable, usable in arithmetic, and
    JSON-serialisable. It additionally carries ``.confidence``, which would
    otherwise force every caller to unpack a tuple or reach into a dataclass.

    Bounds are enforced at construction, so an out-of-range score cannot exist.
    """

    __slots__ = ("_confidence",)

    def __new__(cls, polarity: float, confidence: float) -> Polarity:
        """Implement the __new__ special method."""
        if not math.isfinite(polarity) or not -1.0 <= polarity <= 1.0:
            raise ValueError(f"polarity {polarity} is outside [-1.0, 1.0]")
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence {confidence} is outside [0.0, 1.0]")
        self = super().__new__(cls, polarity)
        self._confidence = float(confidence)  # type: ignore[misc]
        return self

    @property
    def confidence(self) -> float:
        """The calibrated confidence accompanying this polarity."""
        return self._confidence  # type: ignore[attr-defined]

    @property
    def polarity(self) -> float:
        """The signed polarity, as a plain float."""
        return float(self)

    def __repr__(self) -> str:
        """Return the debugging representation."""
        return f"Polarity(polarity={float(self):+.4f}, confidence={self.confidence:.4f})"


def _softmax(logits: Sequence[float]) -> list[float]:
    """Numerically stable softmax."""
    if not logits:
        raise ValueError("logits must not be empty")
    peak = max(logits)
    exps = [math.exp(value - peak) for value in logits]
    total = sum(exps)
    return [value / total for value in exps]


def map_logits(logits: Sequence[float]) -> list[float]:
    """Convert model logits to probabilities, in ``[neg, neu, pos]`` order."""
    return _softmax(logits)


def score_from_probs(neg: float, neu: float, pos: float) -> Polarity:
    """Build a :class:`Polarity` from three class probabilities.

    Polarity is signed so a negative sentiment is representable rather than
    clamped away. Probabilities are normalised first, so overconfident logits
    that do not sum to 1 cannot produce an out-of-range score.
    """
    total = neg + neu + pos
    if total <= 0:
        raise ValueError("probabilities must sum to a positive number")
    neg, neu, pos = neg / total, neu / total, pos / total

    polarity = pos - neg
    # Clamp only against floating-point drift, not to change the meaning.
    polarity = max(-1.0, min(1.0, polarity))
    return Polarity(polarity, max(neg, neu, pos))


class StubModel:
    """A deterministic stand-in for a transformer, used by tests and dry runs.

    Produces probabilities from simple lexical cues, so a scored result is
    reproducible and CI needs no downloaded weights.
    """

    POSITIVE = ("beat", "surge", "record", "upgrade", "rally", "gain", "strong")
    NEGATIVE = ("miss", "plunge", "downgrade", "cut", "loss", "slump", "weak")

    def __init__(self, spec: ModelSpec | None = None) -> None:
        """Initialise the instance."""
        self.spec = spec or get_model("vader")

    def predict(self, text: str) -> list[float]:
        """Return ``[P(neg), P(neu), P(pos)]`` for ``text``."""
        lowered = (text or "").lower()
        positive = sum(word in lowered for word in self.POSITIVE)
        negative = sum(word in lowered for word in self.NEGATIVE)

        if positive == negative == 0:
            return [0.1, 0.8, 0.1]

        # A mild, bounded distribution — the point is a stable, explainable
        # signal, not accuracy.
        p_pos = 0.2 + 0.6 * positive / (positive + negative)
        p_neg = 1.0 - p_pos
        return [p_neg, 0.1, p_pos]

    @property
    def max_length(self) -> int:
        """Handle the max length."""
        return self.spec.max_length


def score_text(
    text: str,
    *,
    model_id: str = "stub",
    temperature: float = 1.0,
) -> tuple[Polarity, list[float], ModelSpec]:
    """Score ``text`` and return ``(polarity, probabilities, model)``.

    ``model_id`` of ``"stub"`` (the default) uses the deterministic lexical
    backend so the function is usable in tests and dry runs. Any other id must
    be in `src/sentiment/models.py`, which is the licence-enforcement point.
    """
    if model_id == "stub":
        spec = ModelSpec(id="stub", revision="0", license="MIT", role="test")
        probabilities = StubModel(spec).predict(text)
    else:
        spec = get_model(model_id)
        probabilities = _predict_with_weights(text, spec)

    from src.sentiment.calibration import TemperatureScaler

    calibrated = TemperatureScaler(temperature=temperature).apply(probabilities)
    return score_from_probs(*calibrated), probabilities, spec


def _predict_with_weights(text: str, spec: ModelSpec) -> list[float]:
    """Run the real transformer. The only place transformers is imported."""
    try:
        from transformers import pipeline  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "real model inference needs `pip install -e .[docs]`-style ML extras; "
            "use model_id='stub' for tests and dry runs"
        ) from exc

    if spec.max_length and len(text) > spec.max_length:
        raise ValueError(
            f"text is {len(text)} characters, over {spec.id}'s {spec.max_length} "
            "context limit. Chunk upstream rather than truncating silently."
        )

    classifier = pipeline("text-classification", model=spec.id, revision=spec.revision)
    results = classifier(text, top_k=3)

    by_label: dict[str, float] = {}
    for item in results if isinstance(results, list) else [results]:
        by_label[item["label"].lower()] = float(item["score"])

    neg = by_label.get("negative", 0.0)
    neu = by_label.get("neutral", 0.0)
    pos = by_label.get("positive", 0.0)
    return [neg, neu, pos]


def batch_score(
    texts: Sequence[str],
    *,
    model_id: str = "stub",
    temperature: float = 1.0,
    threads: int | None = None,
) -> list[Polarity]:
    """Score many texts. ``threads`` caps torch's thread use for CPU inference."""
    if threads:
        try:
            import torch

            torch.set_num_threads(threads)
        except ImportError:
            pass
    return [
        score_text(text, model_id=model_id, temperature=temperature)[0]
        for text in texts
    ]


__all__ = [
    "Polarity",
    "StubModel",
    "batch_score",
    "map_logits",
    "score_from_probs",
    "score_text",
]
