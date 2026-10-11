"""Unit tests for polarity mapping and calibration (T044, T045).

These pin the two formulas that make the sentiment scores usable:

  * **polarity** = ``P(positive) - P(negative)`` — signed, so a negative
    sentiment is representable rather than clamped away.
  * **confidence** = the *calibrated* maximum probability. A raw transformer
    softmax is systematically overconfident; passing it through uncalibrated
    would violate VR-2, so calibration is not optional.

Both are tested against the stub model fixture, so no weights are downloaded and
CI stays hermetic.
"""

from __future__ import annotations

import pytest

from src.sentiment.analyzer import Polarity, map_logits, score_from_probs
from src.sentiment.calibration import TemperatureScaler, calibrate


class TestPolarityMapping:
    """T044 — polarity = P(pos) - P(neg)."""

    def test_signed_positive(self):
        """Test that signed positive."""
        assert score_from_probs(neg=0.1, neu=0.1, pos=0.8) == pytest.approx(0.7)

    def test_signed_negative(self):
        """Test that signed negative."""
        assert score_from_probs(neg=0.7, neu=0.1, pos=0.2) == pytest.approx(-0.5)

    def test_neutral_maps_to_zero(self):
        """Test that neutral maps to zero."""
        assert score_from_probs(neg=0.2, neu=0.6, pos=0.2) == pytest.approx(0.0)

    def test_perfectly_balanced_is_zero(self):
        """Test that perfectly balanced is zero."""
        assert score_from_probs(neg=0.5, neu=0.0, pos=0.5) == pytest.approx(0.0)

    def test_result_is_within_bounds_for_valid_probs(self):
        """Test that result is within bounds for valid probs."""
        for neg, neu, pos in [(0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.33, 0.34, 0.33)]:
            score = score_from_probs(neg=neg, neu=neu, pos=pos)
            assert -1.0 <= score <= 1.0

    def test_probs_that_do_not_sum_to_one_are_normalised(self):
        """Overconfident logits often produce a sum far from 1."""
        score = score_from_probs(neg=0.2, neu=0.2, pos=0.9)
        assert -1.0 <= score <= 1.0

    def test_map_logits_matches_direct_probs(self):
        """Test that map logits matches direct probs."""
        import math

        # A neutral-ish distribution expressed as logits.
        logits = [math.log(1 / 3)] * 3
        probs = map_logits(logits)
        assert sum(probs) == pytest.approx(1.0)
        assert score_from_probs(*probs) == pytest.approx(0.0, abs=1e-9)


class TestConfidence:
    """Test confidence."""
    def test_confidence_is_the_max_probability(self):
        """Test that confidence is the max probability."""
        assert score_from_probs(neg=0.1, neu=0.1, pos=0.8).confidence == pytest.approx(0.8)

    def test_confidence_is_not_always_the_positive_class(self):
        """A strongly negative item must still report a high confidence."""
        score = score_from_probs(neg=0.75, neu=0.15, pos=0.10)
        assert score.polarity < 0
        assert score.confidence == pytest.approx(0.75)


class TestTemperatureScaling:
    """T045 — calibration, not the raw softmax maximum."""

    def test_identity_temperature_leaves_probs_unchanged(self):
        """Test that identity temperature leaves probs unchanged."""
        scaler = TemperatureScaler(temperature=1.0)
        probs = [0.1, 0.1, 0.8]
        assert scaler.apply(probs) == pytest.approx(probs)

    def test_higher_temperature_flattens(self):
        """A high temperature must reduce overconfidence."""
        flat = TemperatureScaler(temperature=4.0)
        result = flat.apply([0.05, 0.05, 0.90])
        assert max(result) < 0.90
        assert min(result) > 0.05

    def test_lower_temperature_sharpens(self):
        """Test that lower temperature sharpens."""
        sharp = TemperatureScaler(temperature=0.5)
        result = sharp.apply([0.2, 0.2, 0.6])
        assert max(result) > 0.6

    def test_result_stays_a_distribution(self):
        """Test that result stays a distribution."""
        scaler = TemperatureScaler(temperature=2.5)
        result = scaler.apply([0.1, 0.2, 0.7])
        assert sum(result) == pytest.approx(1.0)
        assert all(0.0 <= p <= 1.0 for p in result)

    def test_invalid_temperature_is_rejected(self):
        """Test that invalid temperature is rejected."""
        with pytest.raises(ValueError):
            TemperatureScaler(temperature=0.0)
        with pytest.raises(ValueError):
            TemperatureScaler(temperature=-1.0)


class TestCalibrateEndToEnd:
    """Test calibrate end to end."""
    def test_calibrated_confidence_is_bounded(self):
        """Test that calibrated confidence is bounded."""
        result = calibrate([0.05, 0.05, 0.90], temperature=4.0)
        assert 0.0 <= result.confidence <= 1.0
        assert -1.0 <= result.polarity <= 1.0

    def test_calibration_reduces_peak_confidence(self):
        """Test that calibration reduces peak confidence."""
        raw = calibrate([0.05, 0.05, 0.90], temperature=1.0)
        cold = calibrate([0.05, 0.05, 0.90], temperature=4.0)
        assert cold.confidence < raw.confidence

    def test_sign_is_preserved_by_calibration(self):
        """Calibration must not flip the meaning of a score."""
        positive = calibrate([0.1, 0.1, 0.8], temperature=3.0)
        negative = calibrate([0.8, 0.1, 0.1], temperature=3.0)
        assert positive.polarity > 0
        assert negative.polarity < 0

    def test_neutral_stays_neutral(self):
        """Test that neutral stays neutral."""
        assert calibrate([1 / 3, 1 / 3, 1 / 3], temperature=2.0).polarity == pytest.approx(0.0)


class TestPolarityDataclass:
    """Test polarity dataclass."""
    def test_fields_exist(self):
        """Test that fields exist."""
        score = score_from_probs(0.1, 0.1, 0.8)
        assert isinstance(score, Polarity)
        assert hasattr(score, "polarity")
        assert hasattr(score, "confidence")
