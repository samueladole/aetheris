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
REQUESTS: list[tuple[str, str, dict[str, str]]] = [
    ("fred", "series/observations", {"series_id": "GDP"}),
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
        import os

        missing = [
            name
            for name in (
                "FRED_API_KEY",
                "ALPHA_VANTAGE_API_KEY",
                "FINNHUB_API_KEY",
                "FMP_API_KEY",
                "TIINGO_API_KEY",
            )
            if not os.environ.get(name)
        ]
        if missing:
            print(f"error: missing credentials: {', '.join(missing)}")
            return 1

        import vcr

        self.output.mkdir(parents=True, exist_ok=True)

        # Local imports keep this runnable without the package installed.
        from src.config.settings import get_settings
        from src.ingestion.fred_client import FredAdapter
        from src.ingestion.market_client import MarketAdapter
        from src.ingestion.news_client import NewsAdapter
        from src.ingestion.payloads import RawPayloadStore
        from src.ingestion.ratelimit import SharedRateLimiter
        from src.ingestion.registry import IngestionRegistry

        settings = get_settings()
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
                registry.register_adapter(NewsAdapter(provider, api_key=key))

            with vcr.use_cassette(str(cassette), record_mode="once"):
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
