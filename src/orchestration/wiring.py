"""Build the ingestion registry the pipeline and the CLI share.

Adapters are registered for every provider in the spec §3.2.1 registry, each
with its credential from settings, so the registry's fallback order (FR-020)
can reach any provider that declares the requested endpoint.
"""

from __future__ import annotations

from typing import Any

from src.config.settings import Settings
from src.ingestion.base import HttpClient
from src.ingestion.fred_client import FredAdapter
from src.ingestion.market_client import MarketAdapter
from src.ingestion.news_client import ENDPOINTS as NEWS_ENDPOINTS
from src.ingestion.news_client import NewsAdapter
from src.ingestion.registry import IngestionRegistry, build_registry


def _adapters_for(provider: str, api_key: str | None) -> list[Any]:
    """The adapters that serve one provider's endpoints.

    One provider can back both market data and news, so both adapters are
    returned where the provider has a news endpoint, and the registry picks
    whichever handles the requested endpoint.
    """
    if provider == "fred":
        return [FredAdapter(api_key=api_key)]
    adapters: list[Any] = [MarketAdapter(provider, api_key=api_key)]
    if provider in NEWS_ENDPOINTS:
        adapters.append(NewsAdapter(provider, api_key=api_key))
    return adapters


def build_ingestion_registry(
    settings: Settings, *, http: HttpClient | None = None
) -> IngestionRegistry:
    """Return a registry with every provider's adapters registered.

    Args:
        settings: Supplies the raw store path, cache TTL, activity timeout, and
            provider credentials.
        http: HTTP client to use; tests pass a fake so no live call is made.

    Returns:
        A registry whose payload store is ``settings.raw_db_path``.
    """
    registry = build_registry(
        raw_db_path=settings.raw_db_path,
        default_ttl_s=settings.cache_default_ttl_s,
        activity_timeout_s=settings.activity_timeout_s,
        http=http,
    )
    credentials = settings.provider_credentials()
    for spec in registry.providers.all():
        for adapter in _adapters_for(spec.name, credentials.get(spec.name)):
            registry.register_adapter(adapter)
    return registry


__all__ = ["build_ingestion_registry"]
