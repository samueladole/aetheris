"""Cassette safety tests (T041).

Recorded cassettes capture real provider HTTP exchanges, so they are the one
artifact in this repository that can accidentally contain a live credential:
providers pass API keys as query parameters (`token`, `apikey`) or headers
(`Authorization`), and an unfiltered recording writes them straight into the
YAML.

These tests are the guard rail. They fail if a cassette containing a real
credential is ever committed.
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
    @pytest.mark.parametrize("cassette", _cassettes(), ids=lambda p: p.name)
    def test_cassette_has_no_live_credential(self, cassette):
        """Test that cassette has no live credential."""
        creds = _live_credentials()
        if not creds:
            pytest.skip("no credentials configured")

        body = cassette.read_text(encoding="utf-8", errors="replace")
        leaked = [name for name, key in creds.items() if key in body]
        assert not leaked, (
            f"{cassette.name} contains a live credential for: {leaked}. "
            "Cassettes must filter credentials at record time — see "
            "scripts/record_cassettes.py."
        )

    @pytest.mark.parametrize("cassette", _cassettes(), ids=lambda p: p.name)
    def test_cassette_has_no_unredacted_token_param(self, cassette):
        """A token-shaped value must not appear unredacted in any request URI."""
        body = cassette.read_text(encoding="utf-8", errors="replace")
        for param in SECRET_PARAMS:
            # e.g. `?token=sk-abcdef123456` with a plausible key shape.
            pattern = rf"[?&]{param}=[A-Za-z0-9_\-]{{16,}}"
            match = re.search(pattern, body)
            assert not match, (
                f"{cassette.name} exposes {param} unredacted: {match.group(0)[:24]}..."
            )

    @pytest.mark.parametrize("cassette", _cassettes(), ids=lambda p: p.name)
    def test_cassette_has_no_auth_header_value(self, cassette):
        """Test that cassette has no auth header value."""
        body = cassette.read_text(encoding="utf-8", errors="replace")
        for header in SECRET_HEADERS:
            pattern = rf"{header}:\s*\S{{16,}}"
            match = re.search(pattern, body, re.IGNORECASE)
            assert not match, (
                f"{cassette.name} exposes an {header} header value"
            )


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
    def test_cassettes_exist(self):
        """Test that cassettes exist."""
        cassettes = _cassettes()
        assert cassettes, "no cassettes recorded — run scripts/record_cassettes.py"

    def test_every_provider_is_covered(self):
        """At least the four unrestricted providers must appear.

        Not all five can be recorded: `fmp` and `tiingo` return 403 on their
        free "Basic" tiers for several endpoints (verified while recording — see
        tests/fixtures/cassettes/README.md). What is recorded must at minimum
        cover FRED, Alpha Vantage, and Finnhub, plus one of fmp/tiingo.
        """
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
