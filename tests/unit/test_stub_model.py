"""Stub-model fixture tests (T050) — determinism and cue behaviour.

The stub must be *deterministic*: identical text always yields identical
probabilities, because the payload-identity tests depend on it. If the stub ever
became random, the idempotency contract in FR-010 would be untestable.
"""

from __future__ import annotations

import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "models"
sys.path.insert(0, str(FIXTURES))

import stub_sentiment as stub  # noqa: E402


class TestDeterminism:
    """Test determinism."""
    def test_identical_text_identical_result(self):
        """Test that identical text identical result."""
        text = "Company beats expectations as revenue surges"
        assert stub.predict(text) == stub.predict(text)

    def test_result_is_a_probability_triple(self):
        """Test that result is a probability triple."""
        neg, neu, pos = stub.predict("a stock rose")
        assert 0.0 <= neg <= 1.0
        assert 0.0 <= neu <= 1.0
        assert 0.0 <= pos <= 1.0

    def test_neutral_text_is_high_uncertainty(self):
        """Test that neutral text is high uncertainty."""
        neg, neu, pos = stub.predict("nothing happened today")
        assert neu == max(neg, neu, pos)


class TestPolarity:
    """Test polarity."""
    def test_positive_cues_score_positive(self):
        """Test that positive cues score positive."""
        neg, neu, pos = stub.predict("stock surges on record beat")
        assert pos > neg

    def test_negative_cues_score_negative(self):
        """Test that negative cues score negative."""
        neg, neu, pos = stub.predict("stock plunges after loss downgrade")
        assert neg > pos

    def test_mixed_cues_land_between(self):
        """Test that mixed cues land between."""
        neg, neu, pos = stub.predict("surge but also a loss")
        assert -1.0 < (pos - neg) < 1.0

    def test_case_insensitive(self):
        """Test that case insensitive."""
        assert stub.predict("Beats") == stub.predict("BEATS")

    def test_empty_text_is_neutral(self):
        """Test that empty text is neutral."""
        neg, neu, pos = stub.predict("")
        assert neu == max(neg, neu, pos)


class TestSpec:
    """Test spec."""
    def test_reports_a_permissive_licence(self):
        """Lineage must not record a licence Principle I forbids."""
        assert stub.spec()["license"] in {"MIT", "Apache-2.0", "BSD-3-Clause", "ISC"}

    def test_spec_has_the_required_keys(self):
        """Test that spec has the required keys."""
        assert set(stub.spec()) >= {"id", "revision", "license"}

    def test_model_id_is_stable(self):
        """A changing id would silently break payload lineage."""
        assert stub.spec()["id"] == stub.MODEL_ID
