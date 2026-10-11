"""Unit tests for temperature scaling (T045) — VR-2.

A raw transformer softmax is systematically overconfident, so passing its
maximum through as "confidence" would violate VR-2. These tests pin the
properties calibration must have: it reduces overconfidence, never leaves the
distribution, never flips the sign of a polarity, and is deterministic.
"""

from __future__ import annotations

import pytest

from src.sentiment.calibration import (
    TemperatureScaler,
    calibrate,
    fit_temperature,
    softmax,
)


class TestSoftmax:
    """Test softmax."""
    def test_sums_to_one(self):
        """Test that sums to one."""
        assert sum(softmax([1.0, 2.0, 3.0])) == pytest.approx(1.0)

    def test_handles_large_logits_without_overflow(self):
        """Test that handles large logits without overflow."""
        result = softmax([1000.0, 0.0, 0.0])
        assert result[0] == pytest.approx(1.0)

    def test_handles_negative_logits(self):
        """Test that handles negative logits."""
        assert sum(softmax([-5.0, -1.0])) == pytest.approx(1.0)

    def test_rejects_empty(self):
        """Test that rejects empty."""
        with pytest.raises(ValueError):
            softmax([])


class TestTemperatureScalerConstruction:
    """Test temperature scaler construction."""
    def test_rejects_zero(self):
        """Test that rejects zero."""
        with pytest.raises(ValueError):
            TemperatureScaler(temperature=0.0)

    def test_rejects_negative(self):
        """Test that rejects negative."""
        with pytest.raises(ValueError):
            TemperatureScaler(temperature=-2.0)

    def test_rejects_non_finite(self):
        """Test that rejects non finite."""
        with pytest.raises(ValueError):
            TemperatureScaler(temperature=float("inf"))
        with pytest.raises(ValueError):
            TemperatureScaler(temperature=float("nan"))


class TestApply:
    """Test apply."""
    def test_identity_temperature_is_a_no_op(self):
        """Test that identity temperature is a no op."""
        probs = [0.1, 0.2, 0.7]
        assert TemperatureScaler(1.0).apply(probs) == pytest.approx(probs)

    def test_hot_temperature_flattens(self):
        """Test that hot temperature flattens."""
        hot = TemperatureScaler(5.0).apply([0.05, 0.05, 0.90])
        assert max(hot) < 0.90
        assert min(hot) > 0.05

    def test_cold_temperature_sharpens(self):
        """Test that cold temperature sharpens."""
        cold = TemperatureScaler(0.4).apply([0.25, 0.25, 0.50])
        assert max(cold) > 0.50

    def test_result_is_a_distribution(self):
        """Test that result is a distribution."""
        for temperature in (0.2, 1.0, 3.0, 8.0):
            result = TemperatureScaler(temperature).apply([0.1, 0.3, 0.6])
            assert sum(result) == pytest.approx(1.0)
            assert all(0.0 <= p <= 1.0 for p in result)

    def test_monotonicity_is_preserved(self):
        """Calibration must reorder nothing."""
        before = [0.1, 0.3, 0.6]
        after = TemperatureScaler(2.0).apply(before)
        assert after.index(max(after)) == before.index(max(before))
        assert after.index(min(after)) == before.index(min(before))

    def test_rejects_negative_probabilities(self):
        """Test that rejects negative probabilities."""
        with pytest.raises(ValueError):
            TemperatureScaler(1.0).apply([-0.1, 0.5, 0.6])

    def test_rejects_all_zero(self):
        """Test that rejects all zero."""
        with pytest.raises(ValueError):
            TemperatureScaler(1.0).apply([0.0, 0.0, 0.0])

    def test_accepts_unnormalised_input(self):
        """Test that accepts unnormalised input."""
        result = TemperatureScaler(1.0).apply([1, 1, 2])
        assert sum(result) == pytest.approx(1.0)


class TestCalibrate:
    """Test calibrate."""
    def test_returns_a_polarity(self):
        """Test that returns a polarity."""
        from src.sentiment.analyzer import Polarity

        assert isinstance(calibrate([0.05, 0.05, 0.90], temperature=4.0), Polarity)

    def test_reduces_peak_confidence(self):
        """Test that reduces peak confidence."""
        raw = calibrate([0.05, 0.05, 0.90], temperature=1.0).confidence
        cold = calibrate([0.05, 0.05, 0.90], temperature=4.0).confidence
        assert cold < raw

    def test_preserves_sign(self):
        """Test that preserves sign."""
        assert calibrate([0.1, 0.1, 0.8], temperature=3.0) > 0
        assert calibrate([0.8, 0.1, 0.1], temperature=3.0) < 0

    def test_neutral_stays_neutral(self):
        """Test that neutral stays neutral."""
        assert calibrate([1 / 3, 1 / 3, 1 / 3], temperature=2.0) == pytest.approx(0.0)

    def test_detects_logits(self):
        """Values that are not a distribution are treated as logits."""
        logits = [2.0, 0.0, -1.0]
        result = calibrate(logits, temperature=1.0)
        assert -1.0 <= float(result) <= 1.0

    def test_confidence_is_bounded_for_any_temperature(self):
        """Test that confidence is bounded for any temperature."""
        for temperature in (0.1, 1.0, 5.0, 20.0):
            for probs in ([0.05, 0.05, 0.90], [0.9, 0.05, 0.05], [1 / 3] * 3):
                result = calibrate(probs, temperature=temperature)
                assert 0.0 <= result.confidence <= 1.0


class TestFitTemperature:
    """Test fit temperature."""
    def test_recovers_a_known_temperature(self):
        """Fitting against a known target must return approximately its temperature.

        The base distribution is scaled at T to produce the target, and fitting
        must recover T. Fitting against samples *directly* would be circular, so
        this is the meaningful form of the check.
        """
        true_temperature = 3.0
        base = [0.2, 0.2, 0.6]
        target = TemperatureScaler(true_temperature).apply(base)
        fitted = fit_temperature([(base, target)])
        assert fitted == pytest.approx(true_temperature, rel=0.2)

    def test_identity_target_fits_near_one(self):
        """Test that identity target fits near one."""
        base = [0.25, 0.25, 0.50]
        fitted = fit_temperature([(base, list(base))])
        assert fitted == pytest.approx(1.0, abs=0.2)

    def test_is_deterministic(self):
        """Test that is deterministic."""
        base = [0.2, 0.2, 0.6]
        samples = [(base, TemperatureScaler(2.5).apply(base)) for _ in range(5)]
        assert fit_temperature(samples) == fit_temperature(samples)

    def test_rejects_empty_samples(self):
        """Test that rejects empty samples."""
        with pytest.raises(ValueError):
            fit_temperature([])

    def test_rejects_unusable_pairs(self):
        """Test that rejects unusable pairs."""
        with pytest.raises(ValueError):
            fit_temperature([([], [])])

    def test_result_is_within_bounds(self):
        """Test that result is within bounds."""
        base = [0.3, 0.3, 0.4]
        fitted = fit_temperature([(base, TemperatureScaler(2.0).apply(base))])
        assert 0.05 <= fitted <= 10.0
