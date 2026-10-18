"""Deterministic stub sentiment model (T050).

CI and dry runs need inference without a multi-hundred-megabyte download, so
this fixture stands in for a transformer. It is *deterministic*: identical text
always produces identical probabilities, which is what makes the payload
identity tests meaningful.

It is intentionally weak. The point is a stable, explainable signal for
exercising the pipeline — not accuracy. Real scoring uses the manifest in
`src/sentiment/models.py`.
"""

from __future__ import annotations

POSITIVE_CUES = (
    "beat",
    "beats",
    "surge",
    "surging",
    "record",
    "upgrade",
    "upgraded",
    "rally",
    "gain",
    "gains",
    "strong",
    "outperform",
)

NEGATIVE_CUES = (
    "miss",
    "misses",
    "plunge",
    "plunging",
    "downgrade",
    "downgraded",
    "cut",
    "cuts",
    "loss",
    "losses",
    "slump",
    "weak",
    "underperform",
)

# Neutral fallback when no cue matches: deliberately high-uncertainty.
NEUTRAL = (0.10, 0.80, 0.10)

MODEL_ID = "stub"
REVISION = "0"


def predict(text: str) -> tuple[float, float, float]:
    """Return ``(P_negative, P_neutral, P_positive)`` for ``text``.

    Cue counts drive a bounded distribution. The magnitudes are arbitrary but
    fixed: the contract under test is the mapping and the identity, not the
    numbers.
    """
    lowered = (text or "").lower()
    positive = sum(1 for cue in POSITIVE_CUES if cue in lowered)
    negative = sum(1 for cue in NEGATIVE_CUES if cue in lowered)

    if positive == negative == 0:
        return NEUTRAL

    share = positive / (positive + negative)
    p_positive = 0.20 + 0.60 * share
    p_negative = 0.80 - 0.60 * share
    return (p_negative, 0.10, p_positive)


def spec() -> dict[str, str]:
    """The model reference this fixture reports, for payload lineage."""
    return {"id": MODEL_ID, "revision": REVISION, "license": "MIT"}


__all__ = ["MODEL_ID", "REVISION", "predict", "spec"]
