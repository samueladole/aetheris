"""Match news text to watchlist assets before sentiment scoring.

The sentiment layer takes plain ``WatchTerms`` values; the caller converts the
watchlist into them, so this module never imports config or ingestion.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

HEADLINE_WEIGHT = 1.0
SUMMARY_WEIGHT = 0.6


@dataclass(frozen=True)
class WatchTerms:
    """The searchable identifiers for one watched asset.

    Attributes:
        entity_id: Stable identifier of the asset, e.g. ``equity:NASDAQ:AAPL``.
        ticker: Exchange ticker, matched case-sensitively as a whole word.
        names: Company or product names, matched case-insensitively as whole phrases.
    """

    entity_id: str
    ticker: str
    names: tuple[str, ...]


@dataclass(frozen=True)
class EntityMatch:
    """A watched asset mentioned in a news item.

    Attributes:
        entity_id: Identifier of the matched asset.
        weight: ``1.0`` for a headline mention, ``0.6`` for a summary-only mention.
    """

    entity_id: str
    weight: float


def _ticker_pattern(ticker: str) -> re.Pattern[str]:
    """Build the case-sensitive ticker pattern, allowing an optional cashtag ``$``."""
    return re.compile(rf"(?<![A-Za-z0-9])\$?{re.escape(ticker)}(?![A-Za-z0-9])")


def _name_pattern(name: str) -> re.Pattern[str]:
    """Build the case-insensitive whole-phrase pattern for a company name.

    The lookahead excludes apostrophes so possessives like "Apple's" still match,
    while "Applebee's" is rejected by the letter "b" that follows "Apple".
    """
    return re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def _mentions(text: str, terms: WatchTerms) -> bool:
    """Return whether ``text`` mentions the asset by ticker or by any of its names."""
    if _ticker_pattern(terms.ticker).search(text):
        return True
    return any(_name_pattern(name).search(text) for name in terms.names)


def resolve_entities(headline: str, summary: str, terms: Sequence[WatchTerms]) -> list[EntityMatch]:
    """Find the watched assets a news item mentions.

    Each asset appears at most once. A headline mention gives weight
    ``HEADLINE_WEIGHT`` and wins over a summary mention, which gives
    ``SUMMARY_WEIGHT``.

    Args:
        headline: The article headline.
        summary: The article summary or body excerpt.
        terms: The watched assets to look for.

    Returns:
        One ``EntityMatch`` per matched asset, sorted by ``entity_id``.
    """
    matches: list[EntityMatch] = []
    for asset in terms:
        if _mentions(headline, asset):
            matches.append(EntityMatch(asset.entity_id, HEADLINE_WEIGHT))
        elif _mentions(summary, asset):
            matches.append(EntityMatch(asset.entity_id, SUMMARY_WEIGHT))
    return sorted(matches, key=lambda m: m.entity_id)
