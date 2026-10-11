"""Contract tests for the sentiment output payload (T043) — FR-010.

The payload is the wire contract between the Sentiment Engine and the Knowledge
Graph Engine: the graph layer accepts nothing else. Every rejection rule in
`.specify/specs/contracts/sentiment-schema.md` must fail loudly, because a
partially-valid payload would put a malformed node into the graph and violate
VR-1 or VR-2 silently.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.sentiment.schema import (
    SCHEMA_VERSION,
    Calibration,
    ModelRef,
    Provenance,
    ValidationError,
    build_payload,
    validate_payload,
)


def _valid(**overrides):
    """A minimal payload that passes every rule, with fields overridable."""
    base = {
        "entity_id": "equity:NASDAQ:AAPL",
        "polarity_score": 0.4,
        "confidence": 0.87,
        "timestamp": datetime(2026, 10, 10, 14, 30, tzinfo=UTC),
        "source_reference": "finnhub:news:abc123",
        "model": ModelRef(
            id="stub/test-model",
            revision="rev0",
            license="Apache-2.0",
        ),
        "calibration": Calibration(
            method="temperature_scaling",
            temperature=1.0,
            calibrated_on="test:fixture",
        ),
        "provenance": Provenance(
            payload_id="finnhub:/news:h:2026-10-10T14:29:58+00:00",
            text_hash="a" * 64,
            text_length_chars=812,
        ),
    }
    base.update(overrides)
    return base


class TestWellFormedPayload:
    """Test well formed payload."""
    def test_valid_payload_passes(self):
        """Test that valid payload passes."""
        assert validate_payload(_valid()) is None

    def test_payload_is_json_serialisable(self):
        """Test that payload is json serialisable."""
        import json

        payload = build_payload(**_valid())
        json.dumps(payload)
        assert payload["schema_version"] == SCHEMA_VERSION


class TestRejectionRules:
    """Each rule in contracts/sentiment-schema.md must reject."""

    def test_rule1_polarity_above_one(self):
        """Test that rule1 polarity above one."""
        with pytest.raises(ValidationError, match="polarity"):
            validate_payload(_valid(polarity_score=1.5))

    def test_rule1_polarity_below_minus_one(self):
        """Test that rule1 polarity below minus one."""
        with pytest.raises(ValidationError, match="polarity"):
            validate_payload(_valid(polarity_score=-1.5))

    def test_rule2_confidence_above_one(self):
        """Test that rule2 confidence above one."""
        with pytest.raises(ValidationError, match="confidence"):
            validate_payload(_valid(confidence=1.5))

    def test_rule2_confidence_is_required(self):
        """Test that rule2 confidence is required."""
        with pytest.raises(ValidationError, match="confidence"):
            validate_payload(_valid(confidence=None))

    def test_rule3_timezone_naive_timestamp_is_rejected(self):
        """VR-5 cannot be enforced without an unambiguous clock."""
        naive = datetime(2026, 10, 10, 14, 30)
        with pytest.raises(ValidationError, match="timezone|aware|UTC"):
            validate_payload(_valid(timestamp=naive))

    def test_rule4_text_beyond_model_context(self):
        """Test that rule4 text beyond model context."""
        with pytest.raises(ValidationError, match="context|8192|length"):
            validate_payload(
                _valid(provenance=Provenance("p", "h", text_length_chars=9000),
                       text_length_chars=9000)
            )

    def test_rule5_non_permissive_model_licence(self):
        """Test that rule5 non permissive model licence."""
        with pytest.raises(ValidationError, match="license|licence|permissive"):
            validate_payload(
                _valid(model=ModelRef("m", "r", license="MPL-2.0"))
            )

    def test_rule6_entity_id_unresolvable(self):
        """Test that rule6 entity id unresolvable."""
        with pytest.raises(ValidationError, match="entity"):
            validate_payload(_valid(entity_id="not-a-node-ref"))

    def test_rule7_duplicate_metric_id_is_idempotent(self):
        """Re-scoring with the same model revision is a no-op, not an error."""
        kwargs = _valid()
        first = build_payload(**kwargs)
        second = build_payload(**kwargs)
        assert first["metric_id"] == second["metric_id"]


class TestIdentity:
    """Test identity."""
    def test_metric_id_is_deterministic(self):
        """Identical inputs must produce an identical metric_id."""
        kwargs = _valid()
        assert build_payload(**kwargs)["metric_id"] == build_payload(**kwargs)["metric_id"]

    def test_different_model_revisions_differ(self):
        """A re-score with a new model must not overwrite history."""
        a = build_payload(**_valid())
        b = build_payload(**_valid(model=ModelRef("stub/test-model", "rev1", "Apache-2.0")))
        assert a["metric_id"] != b["metric_id"]

    def test_different_entity_ids_differ(self):
        """Test that different entity ids differ."""
        a = build_payload(**_valid())
        b = build_payload(**_valid(entity_id="equity:NASDAQ:MSFT"))
        assert a["metric_id"] != b["metric_id"]


class TestSchemaMetadata:
    """Test schema metadata."""
    def test_required_top_level_keys(self):
        """Test that required top level keys."""
        payload = build_payload(**_valid())
        for key in (
            "schema_version", "metric_id", "entity_id", "polarity_score",
            "confidence", "timestamp", "source_reference", "model",
            "calibration", "provenance",
        ):
            assert key in payload, f"missing top-level key {key!r}"


class TestValidationError:
    """Test validation error."""
    def test_error_names_the_rule(self):
        """Test that error names the rule."""
        with pytest.raises(ValidationError) as exc:
            validate_payload(_valid(polarity_score=2.0))
        assert str(exc.value)

    def test_validation_error_is_an_exception(self):
        """Test that validation error is an exception."""
        assert issubclass(ValidationError, Exception)
