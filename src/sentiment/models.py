"""Pinned model manifest for the sentiment engine (T046).

Constitution Principle I is absolute on model licensing, so the manifest is the
enforcement point: every model that may be scored against must appear here with
its license, and anything absent is refused at load time.

The checkpoints listed in ``research.md`` §D that declare **no license on their
weights** — ``ProsusAI/finbert``, ``yiyanghkust/finbert-tone``,
``ahmedrachid/FinancialBERT-Sentiment-Analysis``,
``cardiffnlp/twitter-roberta-base-sentiment`` — are deliberately **absent**.
Under Hugging Face's terms those are "all rights reserved".
"""

from __future__ import annotations

from dataclasses import dataclass

# Licenses Principle I permits for model weights as well as code.
PERMITTED_LICENSES = frozenset({"Apache-2.0", "MIT", "BSD-3-Clause", "BSD-2-Clause", "ISC"})


class ModelManifestError(RuntimeError):
    """Raised when a requested model is not in the manifest or is not permitted."""


@dataclass(frozen=True)
class ModelSpec:
    """One permitted sentiment model.

    Field names match the payload contract in
    `.specify/specs/contracts/sentiment-schema.md`, so a ``ModelSpec`` can be
    serialised straight into the ``model`` block of a payload.
    """

    id: str
    revision: str
    license: str
    role: str = "unlisted"  # "primary" | "fallback" | "baseline" | "unlisted"
    max_length: int = 0
    notes: str = ""

    @property
    def model_id(self) -> str:
        """Alias for callers that prefer the manifest-style name."""
        return self.id

    @property
    def key(self) -> str:
        """Handle the key."""
        return f"{self.id}@{self.revision}"


# The manifest. Order is preference order.
MANIFEST: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="AnkitAI/FinSense-ModernBERT-Financial-News-Sentiment-Analysis",
        revision="pinned-at-install",
        license="Apache-2.0",
        role="primary",
        max_length=8192,
        notes=(
            "149M params, full news bodies fit without chunking. Accuracy is "
            "benchmarked only by its own author — verify on a held-out set "
            "before trusting it (research.md final risk note)."
        ),
    ),
    ModelSpec(
        id="mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis",
        revision="pinned-at-install",
        license="Apache-2.0",
        role="fallback",
        max_length=512,
        notes="82M params, 98.2% on Financial PhraseBank allagree, battle-tested.",
    ),
    ModelSpec(
        id="vader",
        revision="3.3.2",
        license="MIT",
        role="baseline",
        max_length=0,  # lexicon-based; no context limit
        notes="Deterministic and fast, but too weak to be primary.",
    ),
)

_BY_ID = {m.id: m for m in MANIFEST}
if len(_BY_ID) != len(MANIFEST):  # pragma: no cover - guards against typos
    raise ModelManifestError("duplicate model_id in MANIFEST")


def require(spec: ModelSpec) -> ModelSpec:
    """Assert a manifest entry is licence-compliant. Called at import time."""
    if spec.license not in PERMITTED_LICENSES:
        raise ModelManifestError(
            f"model {spec.model_id!r} declares license {spec.license!r}, which "
            f"Principle I does not permit. Permitted: {sorted(PERMITTED_LICENSES)}"
        )
    return spec


for _spec in MANIFEST:  # fail fast at import, not at first score
    require(_spec)


def get(model_id: str) -> ModelSpec:
    """Look up a model by id, refusing anything not in the manifest."""
    try:
        return _BY_ID[model_id]
    except KeyError as exc:
        raise ModelManifestError(
            f"model {model_id!r} is not in the permitted manifest. Known: "
            f"{sorted(_BY_ID)}. A model absent from the manifest has no verified "
            "license and must not be scored against."
        ) from exc


def primary() -> ModelSpec:
    """The preferred model."""
    return next(m for m in MANIFEST if m.role == "primary")


def names() -> tuple[str, ...]:
    """Every permitted model id."""
    return tuple(m.id for m in MANIFEST)


__all__ = [
    "MANIFEST",
    "PERMITTED_LICENSES",
    "ModelManifestError",
    "ModelSpec",
    "get",
    "names",
    "primary",
]
