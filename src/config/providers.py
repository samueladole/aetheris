"""Provider registry: the single source of truth for free-tier constraints.

The spec (§3.2.1) requires these quota budgets to be **configuration, not code
constants**, so the shared rate limiter reads them from here and they can be
tuned without a code change.

Redistribution status is recorded per provider because three of the five
mandated providers forbid it on their free tiers (see README "Provider terms &
legal boundaries" / plan.md V4).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.config.settings import Settings


class RegistryError(RuntimeError):
    """Raised when the registry is inconsistent or a provider is unknown."""


# ---------------------------------------------------------------------------
# Redistribution policy (plan.md V4 — internal use only for three providers)
# ---------------------------------------------------------------------------
REDISTRIBUTION_OK = "permitted"
REDISTRIBUTION_RESTRICTED = "restricted"
REDISTRIBUTION_FORBIDDEN = "forbidden"


@dataclass(frozen=True)
class ProviderSpec:
    """One row of the spec §3.2.1 registry table.

    ``market_endpoints`` and ``news_endpoints`` are kept separate because a
    provider may declare endpoints in both columns (Finnhub, FMP, Tiingo) and
    the two adapters that serve them are distinct. ``endpoints`` is their union,
    matching the spec table's "Endpoints (v1)" column exactly.
    """

    name: str
    module: str
    market_endpoints: tuple[str, ...]
    news_endpoints: tuple[str, ...]
    # Free-tier limits
    requests_per_minute: int | None
    requests_per_hour: int | None
    requests_per_day: int | None
    # 429/5xx fallback ordering; None = no fallback (single source of truth)
    fallback_order: int | None
    # Licensing / redistribution posture
    redistribution: str
    notes: str = ""

    @property
    def endpoints(self) -> tuple[str, ...]:
        """Handle the endpoints."""
        return self.market_endpoints + self.news_endpoints

    @property
    def has_minute_limit(self) -> bool:
        """Return whether the minute limit is present."""
        return self.requests_per_minute is not None

    def budget_key(self) -> str:
        """Stable key for the rate limiter's per-provider bucket."""
        return f"provider:{self.name}"

    def effective_minute_limit(self) -> int:
        """The tightest per-minute ceiling implied by this provider's quotas."""
        limits = []
        if self.requests_per_minute is not None:
            limits.append(self.requests_per_minute)
        if self.requests_per_hour is not None:
            limits.append(max(1, self.requests_per_hour // 60))
        if self.requests_per_day is not None:
            limits.append(max(1, self.requests_per_day // 1440))
        return min(limits) if limits else 60


# ---------------------------------------------------------------------------
# The registry — mirrors spec.md §3.2.1 exactly
# ---------------------------------------------------------------------------
PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="fred",
        module="src.ingestion.fred_client",
        market_endpoints=("series/observations",),
        news_endpoints=(),
        requests_per_minute=120,
        requests_per_hour=None,
        requests_per_day=None,
        fallback_order=None,
        redistribution=REDISTRIBUTION_RESTRICTED,
        notes=(
            "Commercial use permitted. Redistribution of third-party-copyrighted series "
            "is restricted; attribution string required."
        ),
    ),
    ProviderSpec(
        name="alpha_vantage",
        module="src.ingestion.market_client",
        market_endpoints=("GLOBAL_QUOTE", "FX_DAILY", "CRYPTO_RATING"),
        news_endpoints=(),
        requests_per_minute=None,
        requests_per_hour=None,
        requests_per_day=25,
        fallback_order=3,
        redistribution=REDISTRIBUTION_RESTRICTED,
        notes=(
            "Least restrictive on redistribution but the tightest quota: 25 req/day. "
            "Unlimited only for verified open-source/educational projects on application."
        ),
    ),
    ProviderSpec(
        name="finnhub",
        module="src.ingestion.market_client",
        market_endpoints=("quote", "stock/profile2"),
        news_endpoints=("/news",),
        requests_per_minute=60,
        requests_per_hour=None,
        requests_per_day=None,
        fallback_order=4,
        redistribution=REDISTRIBUTION_FORBIDDEN,
        notes=(
            "Free tier is explicitly labelled 'Personal Use'. Also a global 30 calls/sec "
            "cap across all plans. News history limited to 1 year."
        ),
    ),
    ProviderSpec(
        name="fmp",
        module="src.ingestion.market_client",
        market_endpoints=("quote", "historical-price-full"),
        news_endpoints=("fmp/articles",),
        requests_per_minute=None,
        requests_per_hour=None,
        requests_per_day=250,
        fallback_order=5,
        redistribution=REDISTRIBUTION_FORBIDDEN,
        notes=(
            "Displaying or redistributing data sourced from FMP requires a specific "
            "Data Display and Licensing Agreement. EOD historical only on the free tier."
        ),
    ),
    ProviderSpec(
        name="tiingo",
        module="src.ingestion.market_client",
        market_endpoints=("tiingo/daily", "iex"),
        news_endpoints=("tiingo/news",),
        requests_per_minute=None,
        requests_per_hour=50,
        requests_per_day=1000,
        fallback_order=6,
        redistribution=REDISTRIBUTION_FORBIDDEN,
        notes=(
            "'For Basic and Power accounts, data is for internal and personal use only. "
            "You may not redistribute the data in any form.' Fundamentals API is now a "
            "paid add-on; News API free tier queryable to 3 months."
        ),
    ),
)

_BY_NAME: dict[str, ProviderSpec] = {p.name: p for p in PROVIDERS}
if len(_BY_NAME) != len(PROVIDERS):  # pragma: no cover - guards against typos
    raise RegistryError("Duplicate provider name in PROVIDERS registry")


class ProviderRegistry:
    """Loads the spec §3.2.1 registry and answers capability questions."""

    def __init__(self, providers: Iterable[ProviderSpec] = PROVIDERS) -> None:
        """Initialise the instance."""
        self._providers: tuple[ProviderSpec, ...] = tuple(providers)
        self._by_name = {p.name: p for p in self._providers}
        self._by_endpoint: dict[tuple[str, str], ProviderSpec] = {
            (p.name, ep): p for p in self._providers for ep in p.endpoints
        }

    # -- lookup ----------------------------------------------------------
    def get(self, name: str) -> ProviderSpec:
        """Handle the get."""
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise RegistryError(
                f"Unknown provider {name!r}. Known: {sorted(self._by_name)}"
            ) from exc

    def all(self) -> tuple[ProviderSpec, ...]:
        """Handle the all."""
        return self._providers

    def for_endpoint(self, provider: str, endpoint: str) -> ProviderSpec:
        """Handle the for endpoint."""
        key = (provider, endpoint)
        try:
            return self._by_endpoint[key]
        except KeyError as exc:
            spec = self.get(provider)
            raise RegistryError(
                f"Provider {provider!r} does not declare endpoint {endpoint!r}. "
                f"Declared: {list(spec.endpoints)}"
            ) from exc

    def providers_for_data_type(self, data_type: str) -> list[ProviderSpec]:
        """Providers that can serve ``data_type``, in fallback order.

        ``data_type`` is a coarse capability key: ``macro``, ``prices``,
        ``fundamentals``, or ``news``.
        """
        by_capability = {
            "macro": {"fred"},
            "prices": {"alpha_vantage", "finnhub", "fmp", "tiingo"},
            "fundamentals": {"finnhub", "fmp", "tiingo"},
            "news": {"finnhub", "fmp", "tiingo"},
        }
        if data_type not in by_capability:
            raise RegistryError(
                f"Unknown data_type {data_type!r}. Known: {sorted(by_capability)}"
            )
        matches = [p for p in self._providers if p.name in by_capability[data_type]]
        matches.sort(key=lambda p: (p.fallback_order is None, p.fallback_order or 0))
        return matches

    # -- credentials -----------------------------------------------------
    def required_credentials(self) -> list[str]:
        """All provider names — used by dry-run validation and `status`."""
        return [p.name for p in self._providers]

    def credential_for(self, settings: Settings, provider: str) -> str | None:
        """Read one provider's credential from settings, if set."""
        return settings.provider_credentials().get(provider)

    # -- compliance ------------------------------------------------------
    def redistribution_blockers(self) -> list[ProviderSpec]:
        """Providers whose free tier forbids redistribution (plan.md V4)."""
        return [p for p in self._providers if p.redistribution == REDISTRIBUTION_FORBIDDEN]

    def summary(self) -> list[dict[str, object]]:
        """Serialisable registry view for `aetheris status --verbose`."""
        return [
            {
                "provider": p.name,
                "module": p.module,
                "endpoints": list(p.endpoints),
                "requests_per_minute": p.requests_per_minute,
                "requests_per_hour": p.requests_per_hour,
                "requests_per_day": p.requests_per_day,
                "fallback_order": p.fallback_order,
                "redistribution": p.redistribution,
                "notes": p.notes,
            }
            for p in self._providers
        ]


_DEFAULT = ProviderRegistry()


def get_registry() -> ProviderRegistry:
    """Process-wide default registry instance."""
    return _DEFAULT
