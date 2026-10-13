"""Record VCR cassettes for the five providers in spec §3.2.1.

Run manually with real API keys; never in CI. Each provider/endpoint pair gets
one call and one cassette. Consumes a small amount of free-tier quota, so keep
this manual.

    python scripts/record_cassettes.py --output tests/fixtures/cassettes
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

# The (provider, endpoint, params) pairs to record, drawn from spec §3.2.1.
#
# Quota cost of one pass is deliberately tiny — one call per endpoint, 11 total —
# because free tiers are a shared budget. Alpha Vantage is capped at 25 requests
# per DAY, so its single call is 4% of the daily allowance; Tiingo is the tightest
# binding constraint at 50 requests per hour. Run this once, not per test run.
REQUESTS: list[tuple[str, str, dict[str, str]]] = [
    ("fred", "series/observations", {"series_id": "GDP"}),
    # Alpha Vantage: one call only — 4% of the 25/day free-tier allowance.
    ("alpha_vantage", "GLOBAL_QUOTE", {"symbol": "AAPL"}),
    ("finnhub", "quote", {"symbol": "AAPL"}),
    ("finnhub", "stock/profile2", {"symbol": "AAPL"}),
    ("finnhub", "/news", {"category": "general"}),
    ("fmp", "quote", {"symbol": "AAPL"}),
    ("fmp", "historical-price-full", {"symbol": "AAPL"}),
    ("fmp", "fmp/articles", {}),
    ("tiingo", "tiingo/daily", {"tickers": "AAPL"}),
    ("tiingo", "iex", {"tickers": "AAPL"}),
    ("tiingo", "tiingo/news", {"tickers": "AAPL"}),
]


@dataclass
class Recorder:
    """Recorder."""
    output: Path
    dry_run: bool = False

    def run(self) -> int:
        """Handle the run."""
        # Read credentials through Settings so a git-ignored .env file works.
        # Reading os.environ directly here would report every key as missing
        # even though the CLI finds them.
        from src.config.settings import get_settings

        settings = get_settings()
        excluded = settings.missing_providers(
            ["fred", "alpha_vantage", "finnhub", "fmp", "tiingo"]
        )
        if excluded:
            print(f"error: missing credentials for: {', '.join(excluded)}")
            return 1

        import vcr

        self.output.mkdir(parents=True, exist_ok=True)

        # Local imports keep this runnable without the package installed.
        from src.ingestion.fred_client import FredAdapter
        from src.ingestion.market_client import MarketAdapter
        from src.ingestion.news_client import NewsAdapter
        from src.ingestion.payloads import RawPayloadStore
        from src.ingestion.ratelimit import SharedRateLimiter
        from src.ingestion.registry import IngestionRegistry

        recorded = 0

        for provider, endpoint, params in REQUESTS:
            cassette = self.output / f"{provider}_{endpoint.replace('/', '_')}.yaml"
            print(f"recording {provider}::{endpoint} -> {cassette.name}")

            if self.dry_run:
                continue

            registry = IngestionRegistry(
                payload_store=RawPayloadStore(":memory:"),
                rate_limiter=SharedRateLimiter(),
                activity_timeout_s=30.0,
            )
            key = settings.provider_credentials().get(provider)
            if provider == "fred":
                registry.register_adapter(FredAdapter(api_key=key))
            else:
                registry.register_adapter(MarketAdapter(provider, api_key=key))
                # Only register the news adapter for providers that actually serve
                # news. Alpha Vantage is deliberately excluded (its NEWS_SENTIMENT
                # endpoint returns vendor-computed sentiment), and constructing the
                # adapter for it raises.
                from src.ingestion.news_client import ENDPOINTS as NEWS_ENDPOINTS

                if provider in NEWS_ENDPOINTS:
                    registry.register_adapter(NewsAdapter(provider, api_key=key))

            with vcr.use_cassette(
                str(cassette),
                record_mode="once",
                # Credentials must never reach disk. Providers put keys in query
                # params (Finnhub `token`, FMP/Alpha Vantage `apikey`) and
                # Tiingo uses an Authorization header; an unfiltered cassette
                # records the real key in the request URI. vcr applies the same
                # filter on replay, so recorded and outgoing requests still match.
                filter_query_parameters=["token", "apikey", "api_key", "apiKey"],
                filter_headers=["authorization", "x-api-key", "api-key"],
            ):
                try:
                    registry.fetch(
                        _data_type(provider, endpoint),
                        endpoint=endpoint,
                        params=params,
                    )
                    recorded += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"  warning: {exc}")
                    if cassette.exists():
                        cassette.unlink()

        print(f"recorded {recorded}/{len(REQUESTS)} cassettes into {self.output}")
        return 0


def _data_type(provider: str, endpoint: str) -> str:
    if provider == "fred":
        return "macro"
    if endpoint in {"/news", "fmp/articles", "tiingo/news"}:
        return "news"
    if endpoint == "stock/profile2":
        return "fundamentals"
    return "prices"


def main(argv: list[str] | None = None) -> int:
    """Handle the main."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="tests/fixtures/cassettes",
        help="Directory to write cassettes into",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the requests without recording (no quota consumed)",
    )
    args = parser.parse_args(argv)
    return Recorder(Path(args.output), dry_run=args.dry_run).run()


if __name__ == "__main__":
    sys.exit(main())
