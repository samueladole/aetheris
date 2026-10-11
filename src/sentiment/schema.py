"""Sentiment payload builder and validator (T049) — FR-010, VR-1, VR-2.

The single place a scored item becomes the payload the graph layer accepts.
Validation is exhaustive and fails loudly: a partially-valid payload would put
a malformed node into the graph, and VR-1/VR-2 would be violated silently.

Rejection rules are numbered to match
`.specify/specs/contracts/sentiment-schema.md` and are tested in
`tests/contract/test_sentiment_schema.py`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.sentiment.models import ModelSpec as ModelRef  # re-exported for callers

SCHEMA_VERSION = "1.0"

# Rule 4: the primary model's context window. Text beyond this must be chunked
# upstream — silently truncating would change what was scored.
MAX_TEXT_CHARS = 8192

# Rule 6: a usable entity reference. Matches the forms data-model.md defines:
# `<asset_class>:<exchange>:<symbol>` or `macro:<indicator_code>`.
_ENTITY_PATTERN = re.compile(
    r"^(?:equity|forex|crypto|index):[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+$|^macro:[A-Za-z0-9_.-]+$"
)

# Rule 5: Principle I. Mirrors src/sentiment/models.PERMITTED_LICENSES.
PERMITTED_LICENSES = frozenset(
    {"Apache-2.0", "MIT", "BSD-3-Clause", "BSD-2-Clause", "ISC"}
)


class ValidationError(ValueError):
    """A payload violated a rule in contracts/sentiment-schema.md."""


@dataclass(frozen=True)
class Calibration:
    """How the raw score was turned into a calibrated confidence."""

    method: str
    temperature: float
    calibrated_on: str

    def as_dict(self) -> dict[str, Any]:
        """Handle the as dict."""
        return {
            "method": self.method,
            "temperature": self.temperature,
            "calibrated_on": self.calibrated_on,
        }


@dataclass(frozen=True)
class Provenance:
    """Everything needed to trace a score back to its source text."""

    payload_id: str
    text_hash: str
    text_length_chars: int

    def as_dict(self) -> dict[str, Any]:
        """Handle the as dict."""
        return {
            "payload_id": self.payload_id,
            "text_hash": self.text_hash,
            "text_length_chars": self.text_length_chars,
        }


def normalise_text(text: str) -> str:
    """Canonical form used for the text hash, so republished items deduplicate."""
    return re.sub(r"\s+", " ", text or "").strip()


def text_hash(text: str) -> str:
    """SHA-256 of the normalised text."""
    return hashlib.sha256(normalise_text(text).encode()).hexdigest()


def metric_id_for(
    entity_id: str,
    source_reference: str,
    hash_value: str,
    model: ModelRef | None = None,
) -> str:
    """Metric identity: ``${entity_id}:${source_reference}:${text_hash}``.

    When a model is supplied its revision is appended. That is deliberate: two
    models scoring the same text produce different scores, and without the
    revision in the identity the second write would overwrite the first —
    exactly the lineage loss data-model.md §1.3 forbids. Omit the model only
    when constructing an identity for a model-agnostic lookup.
    """
    base = f"{entity_id}:{source_reference}:{hash_value}"
    if model is None:
        return base
    return f"{base}:{model.id}@{model.revision}"


def validate_payload(fields: dict[str, Any]) -> None:
    """Apply every rejection rule. Returns None if valid, raises otherwise."""
    entity_id = fields.get("entity_id")
    if not entity_id or not _ENTITY_PATTERN.match(str(entity_id)):
        raise ValidationError(
            f"rule 6: entity_id {entity_id!r} does not resolve to a node. Expected "
            "`<asset_class>:<exchange>:<symbol>` or `macro:<indicator_code>`."
        )

    # Rule 1: signed, bounded polarity.
    score = fields.get("polarity_score")
    if score is None or not isinstance(score, (int, float)):
        raise ValidationError(f"rule 1: polarity_score must be a number, got {score!r}")
    if not -1.0 <= float(score) <= 1.0:
        raise ValidationError(
            f"rule 1: polarity_score {score} is outside [-1.0, 1.0]"
        )

    # Rule 2: bounded, calibrated confidence.
    confidence = fields.get("confidence")
    if confidence is None or not isinstance(confidence, (int, float)):
        raise ValidationError(
            f"rule 2: confidence must be a number, got {confidence!r}"
        )
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValidationError(
            f"rule 2: confidence {confidence} is outside [0.0, 1.0]"
        )

    calibration = fields.get("calibration")
    if calibration is None:
        raise ValidationError(
            "rule 2: confidence must be calibrated. An uncalibrated softmax "
            "maximum is not a confidence (VR-2)."
        )

    # Rule 3: an unambiguous clock is required to enforce the no-lookahead
    # invariant (VR-5), so a naive datetime is a defect, not a convenience.
    timestamp = fields.get("timestamp")
    if not isinstance(timestamp, datetime):
        raise ValidationError(
            f"rule 3: timestamp must be a datetime, got {type(timestamp).__name__}"
        )
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValidationError(
            "rule 3: timestamp is timezone-naive. The no-lookahead invariant "
            "(VR-5) cannot be enforced without an unambiguous clock."
        )

    # Rule 5: model licensing.
    model = fields.get("model")
    if model is None or getattr(model, "license", None) not in PERMITTED_LICENSES:
        raise ValidationError(
            f"rule 5: model license {getattr(model, 'license', None)!r} is not "
            f"permissive. Permitted: {sorted(PERMITTED_LICENSES)} (Principle I)."
        )

    # Rule 4: text length must fit the model's context window.
    provenance = fields.get("provenance")
    if provenance is None or getattr(provenance, "text_length_chars", None) is None:
        raise ValidationError("rule 4: provenance with text_length_chars is required")
    length = int(provenance.text_length_chars)
    if length > MAX_TEXT_CHARS:
        raise ValidationError(
            f"rule 4: text is {length} characters, over the {MAX_TEXT_CHARS} "
            "context limit. Chunk upstream; do not silently truncate."
        )

    source_reference = fields.get("source_reference")
    if not source_reference:
        raise ValidationError("source_reference is required for traceability")


def build_payload(
    *,
    entity_id: str,
    polarity_score: float,
    confidence: float,
    timestamp: datetime,
    source_reference: str,
    model: ModelRef,
    calibration: Calibration,
    provenance: Provenance,
    text_hash_value: str | None = None,
    text: str | None = None,
) -> dict[str, Any]:
    """Build and validate a payload, returning the JSON-serialisable form.

    ``text`` or ``text_hash_value`` supplies the hash. Rebuilding with identical
    inputs yields an identical ``metric_id`` — that idempotency is what makes a
    re-score with the same model revision a no-op rather than a duplicate.
    """
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    timestamp = timestamp.astimezone(UTC)

    if text_hash_value is None:
        if text is not None:
            text_hash_value = text_hash(text)
        elif provenance is not None and getattr(provenance, "text_hash", None):
            # The hash may already be recorded in provenance; reuse it rather
            # than requiring callers to pass it twice.
            text_hash_value = provenance.text_hash
        else:
            raise ValidationError(
                "either text or a text hash is required to build a metric_id"
            )

    md = metric_id_for(entity_id, source_reference, text_hash_value, model)

    fields: dict[str, Any] = {
        "entity_id": entity_id,
        "polarity_score": float(polarity_score),
        "confidence": float(confidence),
        "timestamp": timestamp,
        "source_reference": source_reference,
        "model": model,
        "calibration": calibration,
        "provenance": provenance,
    }
    validate_payload(fields)

    return {
        "schema_version": SCHEMA_VERSION,
        "metric_id": md,
        "entity_id": entity_id,
        "polarity_score": float(polarity_score),
        "confidence": float(confidence),
        "timestamp": timestamp.isoformat(),
        "source_reference": source_reference,
        "model": {
            "id": model.id,
            "revision": model.revision,
            "license": model.license,
        },
        "calibration": calibration.as_dict(),
        "provenance": provenance.as_dict(),
    }


def summarise(payload: dict[str, Any]) -> str:
    """One-line human summary, used by `aetheris score` output."""
    return (
        f"{payload['entity_id']} score={payload['polarity_score']:+.4f} "
        f"confidence={payload['confidence']:.4f} model={payload['model']['id']}"
    )


__all__ = [
    "Calibration",
    "MAX_TEXT_CHARS",
    "PERMITTED_LICENSES",
    "Provenance",
    "SCHEMA_VERSION",
    "ValidationError",
    "build_payload",
    "metric_id_for",
    "normalise_text",
    "summarise",
    "text_hash",
    "validate_payload",
]
