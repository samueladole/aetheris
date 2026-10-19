"""Unit tests for matching news text to watchlist assets."""

from __future__ import annotations

import pytest

from src.sentiment.entities import EntityMatch, WatchTerms, resolve_entities

AAPL = WatchTerms("equity:NASDAQ:AAPL", "AAPL", ("Apple",))
KO = WatchTerms("equity:NYSE:KO", "KO", ("Coca-Cola",))


def test_headline_beats_summary():
    """A headline mention of a name gives weight 1.0 even if the summary also mentions it."""
    assert resolve_entities("Apple beats estimates", "AAPL rose", [AAPL]) == [
        EntityMatch("equity:NASDAQ:AAPL", 1.0)
    ]


def test_summary_only_match():
    """A mention only in the summary, by cashtag, gives weight 0.6."""
    assert resolve_entities("Markets rally", "Shares of $AAPL rose", [AAPL]) == [
        EntityMatch("equity:NASDAQ:AAPL", 0.6)
    ]


@pytest.mark.parametrize("text", ["KOREA exports rise", "Kodak files", "a ko punch"])
def test_ticker_needs_a_whole_word_in_its_own_case(text):
    """A ticker matches only as an upper-case whole word, not inside another word."""
    assert resolve_entities(text, "", [KO]) == []


def test_names_are_case_insensitive_whole_phrases():
    """Names match regardless of case, and a longer word that starts with the name does not."""
    assert resolve_entities("COCA-COLA raises dividend", "", [KO])[0].weight == 1.0
    assert resolve_entities("Applebee's expands", "", [AAPL]) == []


def test_possessive_name_still_matches():
    """A possessive mention such as "Apple's" is a genuine mention and matches at weight 1.0."""
    assert resolve_entities("Apple's results beat", "", [AAPL]) == [
        EntityMatch("equity:NASDAQ:AAPL", 1.0)
    ]


def test_several_entities_sorted():
    """Matches for several entities come back sorted by entity_id."""
    matches = resolve_entities("Coca-Cola and Apple both gain", "", [KO, AAPL])
    assert [m.entity_id for m in matches] == ["equity:NASDAQ:AAPL", "equity:NYSE:KO"]


def test_no_match():
    """Text that mentions neither a name nor a ticker yields no matches."""
    assert resolve_entities("Bonds quiet today", "Nothing much", [AAPL, KO]) == []
