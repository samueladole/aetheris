"""Cassette safety tests (T041).

Recorded cassettes capture real provider HTTP exchanges, so they are the one
artifact in this repository that can accidentally contain a live credential:
providers pass API keys as query parameters (`token`, `apikey`) or headers
(`Authorization`), and an unfiltered recording writes them straight into the
YAML.

These tests are the guard rail. They fail if a cassette containing a real
credential is ever committed.

Cassettes are git-ignored: they hold provider data that Finnhub, FMP, and
Tiingo's free-tier terms forbid redistributing, and this repository is public.
So each check loops over whatever cassettes exist rather than parametrizing on
them, which keeps the collected test count the same in a clean checkout.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CASSETTES = REPO / "tests" / "fixtures" / "cassettes"

# Query parameters and headers that carry credentials.
SECRET_PARAMS = ("token", "apikey", "api_key", "apiKey")
SECRET_HEADERS = ("authorization", "x-api-key", "api-key")


def _cassettes() -> list[Path]:
    if not CASSETTES.is_dir():
        return []
    return sorted(CASSETTES.glob("*.yaml"))


def _live_credentials() -> dict[str, str]:
    """Configured credentials, if a local .env is present."""
    from src.config.settings import Settings

    try:
        creds = Settings.load().provider_credentials()
    except Exception:  # noqa: BLE001 - no credentials configured is fine
        return {}
    return {k: v for k, v in creds.items() if v and len(v) >= 8}


class TestNoCredentialInCassettes:
    """Test no credential in cassettes."""

    def test_no_cassette_has_a_live_credential(self):
        """Test that no cassette contains a configured credential."""
        creds = _live_credentials()
        if not creds:
            pytest.skip("no credentials configured")

        leaks = {}
        for cassette in _cassettes():
            body = cassette.read_text(encoding="utf-8", errors="replace")
            leaked = [name for name, key in creds.items() if key in body]
            if leaked:
                leaks[cassette.name] = leaked
        assert not leaks, (
            f"cassettes contain live credentials: {leaks}. Cassettes must filter "
            "credentials at record time — see scripts/record_cassettes.py."
        )

    def test_no_cassette_has_an_unredacted_token_param(self):
        """A token-shaped value must not appear unredacted in any request URI."""
        exposed = []
        for cassette in _cassettes():
            body = cassette.read_text(encoding="utf-8", errors="replace")
            for param in SECRET_PARAMS:
                # e.g. `?token=sk-abcdef123456` with a plausible key shape.
                match = re.search(rf"[?&]{param}=[A-Za-z0-9_\-]{{16,}}", body)
                if match:
                    exposed.append(f"{cassette.name}: {match.group(0)[:24]}...")
        assert not exposed, f"unredacted credential parameters: {exposed}"

    def test_no_cassette_has_an_auth_header_value(self):
        """Test that no cassette records an auth header value."""
        exposed = []
        for cassette in _cassettes():
            body = cassette.read_text(encoding="utf-8", errors="replace")
            for header in SECRET_HEADERS:
                if re.search(rf"{header}:\s*\S{{16,}}", body, re.IGNORECASE):
                    exposed.append(f"{cassette.name}: {header}")
        assert not exposed, f"cassettes expose auth header values: {exposed}"


class TestRecordConfiguration:
    """Test record configuration."""
    def test_recorder_filters_credentials(self):
        """The recorder must configure vcr to filter secrets.

        The guard above only catches leaks that exist; this catches the
        configuration that would create one.
        """
        recorder = REPO / "scripts" / "record_cassettes.py"
        assert recorder.is_file()
        text = recorder.read_text(encoding="utf-8")
        assert "filter_query_parameters" in text, (
            "the cassette recorder does not filter query-parameter credentials"
        )
        assert "filter_headers" in text, (
            "the cassette recorder does not filter header credentials"
        )


class TestCassetteInventory:
    """Test cassette inventory."""

    def test_every_provider_is_covered(self):
        """At least the four unrestricted providers must appear.

        Not all five can be recorded: `fmp` and `tiingo` return 403 on their
        free "Basic" tiers for several endpoints (verified while recording — see
        tests/fixtures/cassettes/README.md). What is recorded must at minimum
        cover FRED, Alpha Vantage, and Finnhub, plus one of fmp/tiingo.
        """
        if not _cassettes():
            pytest.skip(
                "no cassettes: they are git-ignored (provider redistribution "
                "terms), so record them locally with scripts/record_cassettes.py"
            )
        required = {"fred", "alpha_vantage", "finnhub"}
        optional = {"fmp", "tiingo"}
        all_providers = required | optional

        found = set()
        for cassette in _cassettes():
            stem = cassette.name.split(".")[0]
            # Match on the longest provider prefix, because names contain
            # underscores (alpha_vantage_GLOBAL_QUOTE).
            for provider in all_providers:
                if stem == provider or stem.startswith(f"{provider}_"):
                    found.add(provider)
                    break

        assert required <= found, (
            f"providers with no cassette: {sorted(required - found)}"
        )
        assert found & optional, "neither fmp nor tiingo was recorded"
