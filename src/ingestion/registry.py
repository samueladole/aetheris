"""Provider selection, caching, and fallback (T033/T038/T040).

The registry is the single place that decides *which* provider serves a data
type and in what order, per spec §3.2.1. It owns three behaviours:

  1. **Cache-before-fetch** (FR-007, spec §4 step 2): a fresh cached payload is
     served and no provider call is made.
  2. **Rate limiting** (FR-008): the shared token bucket is acquired before any
     request, so concurrent workers collectively respect the free-tier ceiling.
  3. **Fallback** (FR-020): on `ProviderUnavailable`, the next non-exhausted
     provider for the same data type is tried, and the failure is recorded as a
     span event.

Retry/backoff is deliberately **not** implemented here. Invariant A3 forbids the
ingestion layer from importing orchestration, so the backoff policy is *injected*
by the caller (the orchestration layer) as a duck-typed object. Standalone CLI
use passes no policy, which means one attempt per provider — the orchestration
layer supplies bounded retries for scheduled runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.config.providers import ProviderRegistry, ProviderSpec, get_registry
from src.ingestion.base import (
    FetchRequest,
    HttpClient,
    ProviderUnavailable,
    RequestsHttpClient,
    to_raw_payload,
)
from src.ingestion.cache import TTLCache
from src.ingestion.payloads import RawPayload, RawPayloadStore
from src.ingestion.ratelimit import SharedRateLimiter
from src.observability.metrics import (
    CACHE_HITS,
    CACHE_MISSES,
    FETCH_ERRORS,
    FETCH_FALLBACKS,
    FETCH_TOTAL,
    QUOTA_REMAINING,
    get_metrics,
)
from src.observability.telemetry import span, span_event

# Adapter state machine from data-model.md §5.
ADAPTER_HEALTHY = "HEALTHY"
ADAPTER_RATE_LIMITED = "RATE_LIMITED"
ADAPTER_EXHAUSTED = "EXHAUSTED"


class RegistryError(RuntimeError):
    """Raised when the registry cannot serve a data type."""


class RetryPolicy(Protocol):
    """Duck-typed backoff policy, injected by the orchestration layer.

    Implementing this as a Protocol (rather than importing
    `src.orchestration.retry`) is what keeps the ingestion layer importable
    without orchestration (invariant A5 / A3).
    """

    max_attempts: int
    base_delay_ms: int

    def delay_before_attempt(self, attempt: int) -> int:
        """Milliseconds to wait before ``attempt``; 0 for the first."""
        ...


@dataclass
class FetchOutcome:
    """Result of one registry fetch, including provenance of which provider won."""

    payload: RawPayload
    provider: str
    served_from_cache: bool = False
    attempts: int = 1
    fallbacks: int = 0

    @property
    def cache_hit(self) -> bool:
        """Handle the cache hit."""
        return self.served_from_cache


@dataclass
class IngestionRegistry:
    """Serves data types from the first available provider, in fallback order."""

    providers: ProviderRegistry = field(default_factory=get_registry)
    payload_store: RawPayloadStore | None = None
    cache: TTLCache | None = None
    rate_limiter: SharedRateLimiter = field(default_factory=SharedRateLimiter)
    http: HttpClient = field(default_factory=RequestsHttpClient)
    activity_timeout_s: float = 30.0
    _adapter_state: dict[str, str] = field(default_factory=dict, init=False)
    _adapters: dict[str, Any] = field(default_factory=dict, init=False)

    # ------------------------------------------------------------------
    # Adapter wiring
    # ------------------------------------------------------------------
    def register_adapter(self, adapter: Any) -> None:
        """Attach a concrete adapter to the registry.

        Imported lazily by the caller (`src/cli/main.py` or the orchestration
        layer) so this module never imports a specific adapter implementation.
        """
        provider = getattr(adapter, "provider", None)
        if not provider:
            raise RegistryError("adapter must declare a `provider` attribute")
        self.providers.get(provider)  # validates against the spec §3.2.1 registry
        self._adapters[provider] = adapter

    def adapter_for(self, provider: str) -> Any:
        """Handle the adapter for."""
        try:
            return self._adapters[provider]
        except KeyError as exc:
            raise RegistryError(
                f"no adapter registered for {provider!r}; "
                f"registered: {sorted(self._adapters) or 'none'}"
            ) from exc

    # ------------------------------------------------------------------
    # Adapter state machine (data-model.md §5)
    # ------------------------------------------------------------------
    def adapter_state(self, provider: str) -> str:
        """Handle the adapter state."""
        return self._adapter_state.get(provider, ADAPTER_HEALTHY)

    def _set_adapter_state(self, provider: str, state: str) -> None:
        if self._adapter_state.get(provider) != state:
            self._adapter_state[provider] = state
            span_event(
                "adapter.state",
                **{"aetheris.provider": provider, "aetheris.state": state},
            )

    def is_exhausted(self, provider: str) -> bool:
        """Return whether this is exhausted."""
        return self.adapter_state(provider) == ADAPTER_EXHAUSTED

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------
    def resolve(self, data_type: str, endpoint: str | None = None) -> list[ProviderSpec]:
        """Providers that can serve ``data_type``, in fallback order."""
        specs = self.providers.providers_for_data_type(data_type)
        if endpoint:
            specs = [s for s in specs if endpoint in s.endpoints]
            if not specs:
                raise RegistryError(
                    f"no provider declares endpoint {endpoint!r} for {data_type!r}"
                )
        return specs

    def fetch(
        self,
        data_type: str,
        *,
        endpoint: str | None = None,
        params: Mapping[str, str] | None = None,
        ttl_seconds: int | None = None,
        force: bool = False,
        retry_policy: RetryPolicy | None = None,
    ) -> FetchOutcome:
        """Fetch a data type, honouring cache, rate limits, and fallback order.

        ``retry_policy`` is injected by the orchestration layer; when omitted the
        registry makes a single attempt per provider and leaves retrying to the
        durable workflow.
        """
        params = dict(params or {})
        specs = self.resolve(data_type, endpoint)
        metrics = get_metrics()
        fallbacks = 0
        last_error: Exception | None = None

        for spec in specs:
            if self.is_exhausted(spec.name):
                continue

            ttl = ttl_seconds
            if ttl is None and self.cache is not None:
                ttl = self.cache.ttl_for()

            # --- cache first (FR-007) -----------------------------------
            request = self._build_request(spec, data_type, endpoint, params)
            if not force and self.cache is not None:
                outcome = self.cache.get(
                    spec.name,
                    endpoint or data_type,
                    request.request_hash,
                    ttl_override=ttl,
                )
                if outcome.hit and outcome.payload is not None:
                    metrics.cache_hits.add(1)
                    return FetchOutcome(
                        payload=outcome.payload,
                        provider=spec.name,
                        served_from_cache=True,
                        fallbacks=fallbacks,
                    )

            # --- rate limit (FR-008) ------------------------------------
            with span(
                "ingestion.fetch",
                **{
                    "aetheris.provider": spec.name,
                    "aetheris.endpoint": request.endpoint,
                },
            ):
                try:
                    self.rate_limiter.acquire(spec, timeout_s=self.activity_timeout_s)
                except TimeoutError as exc:
                    self._set_adapter_state(spec.name, ADAPTER_RATE_LIMITED)
                    metrics.fetch_errors.add(1)
                    raise ProviderUnavailable(
                        spec.name, request.endpoint, None, f"rate limiter timeout: {exc}"
                    ) from exc

                # --- request, with optional injected retry ---------------
                attempts = 1
                response = None
                while True:
                    metrics.fetch_total.add(1)
                    try:
                        response = self._get(spec, request)
                        self._set_adapter_state(spec.name, ADAPTER_HEALTHY)
                        break
                    except ProviderUnavailable as exc:
                        metrics.fetch_errors.add(1)
                        last_error = exc
                        if exc.retryable:
                            self._set_adapter_state(spec.name, ADAPTER_RATE_LIMITED)
                        else:
                            self._set_adapter_state(spec.name, ADAPTER_EXHAUSTED)
                            break
                        if retry_policy is None:
                            break
                        if attempts >= retry_policy.max_attempts:
                            self._set_adapter_state(spec.name, ADAPTER_EXHAUSTED)
                            break
                        delay_ms = retry_policy.delay_before_attempt(attempts + 1)
                        if delay_ms > 0:
                            import time

                            time.sleep(delay_ms / 1000.0)
                        attempts += 1

                if response is None:
                    # This provider is unavailable; fall through to the next.
                    if spec.fallback_order is not None:
                        fallbacks += 1
                        metrics.fetch_fallbacks.add(1)
                        span_event(
                            "ingestion.fallback",
                            **{
                                "aetheris.provider": spec.name,
                                "aetheris.reason": str(last_error),
                            },
                        )
                        continue
                    # No further providers to try: re-raise the original error
                    # verbatim so the message is not wrapped twice.
                    if isinstance(last_error, ProviderUnavailable):
                        raise last_error
                    raise ProviderUnavailable(
                        spec.name,
                        request.endpoint,
                        getattr(last_error, "status_code", None),
                        str(last_error),
                    )

                # --- persist immutably and return ------------------------
                payload = to_raw_payload(request, response, ttl)
                if self.payload_store is not None:
                    stored, created = self.payload_store.store(
                        provider=payload.provider,
                        endpoint=payload.endpoint,
                        request_hash=payload.request_hash,
                        body=payload.body,
                        fetched_at=payload.fetched_at,
                        ttl_seconds=payload.ttl_seconds,
                    )
                    payload = stored
                    if self.cache is not None:
                        self.cache.record_store(spec.name)

                return FetchOutcome(
                    payload=payload,
                    provider=spec.name,
                    served_from_cache=False,
                    attempts=attempts,
                    fallbacks=fallbacks,
                )

        raise ProviderUnavailable(
            "none",
            endpoint or data_type,
            getattr(last_error, "status_code", None),
            f"no provider could serve {data_type!r}; tried "
            f"{[s.name for s in specs]}",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _build_request(
        self, spec: ProviderSpec, data_type: str, endpoint: str | None,
        params: Mapping[str, str],
    ) -> FetchRequest:
        adapter = self._adapters.get(spec.name)
        target_endpoint = endpoint or data_type
        if adapter is None:
            # No adapter registered: build a generic request so the cache can
            # still key on the data type. Useful for dry runs and status checks.
            from src.ingestion.base import FetchRequest as FR

            return FR.create(spec.name, target_endpoint, spec.module, params)
        return adapter.build_request(target_endpoint, params)

    def _get(self, spec: ProviderSpec, request: FetchRequest) -> Any:
        adapter = self._adapters.get(spec.name)
        if adapter is None:
            raise RegistryError(
                f"no adapter registered for {spec.name!r}; cannot issue a request"
            )
        response = self.http.get(
            request.url,
            params=request.params,
            headers=adapter.headers(),
            timeout=self.activity_timeout_s,
        )
        response.raise_for_unavailable(spec.name, request.endpoint)
        return response

    # ------------------------------------------------------------------
    # Introspection for `aetheris status`
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """Return the current snapshot."""
        cache_stats = self.cache.stats() if self.cache else {}
        quota = self.rate_limiter.snapshot()
        return {
            "adapters": {
                name: {
                    "state": self.adapter_state(name),
                    "exhausted": self.is_exhausted(name),
                }
                for name in sorted(self._adapters)
            },
            "cache": cache_stats,
            "rate_limiter_remaining": quota,
        }


def build_registry(
    *,
    raw_db_path: str = "./aetheris-raw.db",
    default_ttl_s: int = 3600,
    activity_timeout_s: float = 30.0,
    http: HttpClient | None = None,
) -> IngestionRegistry:
    """Construct a fully-wired registry (used by the CLI)."""
    store = RawPayloadStore(raw_db_path)
    cache = TTLCache(store, default_ttl_s=default_ttl_s)
    return IngestionRegistry(
        payload_store=store,
        cache=cache,
        activity_timeout_s=activity_timeout_s,
        http=http or RequestsHttpClient(),
    )


def metric_names() -> dict[str, str]:
    """The metric names this layer records — referenced by quickstart.md."""
    return {
        "fetch_total": FETCH_TOTAL,
        "fetch_errors": FETCH_ERRORS,
        "fallbacks": FETCH_FALLBACKS,
        "cache_hits": CACHE_HITS,
        "cache_misses": CACHE_MISSES,
        "quota_remaining": QUOTA_REMAINING,
    }


__all__ = [
    "ADAPTER_EXHAUSTED",
    "ADAPTER_HEALTHY",
    "ADAPTER_RATE_LIMITED",
    "FetchOutcome",
    "IngestionRegistry",
    "RegistryError",
    "RetryPolicy",
    "build_registry",
    "metric_names",
]
