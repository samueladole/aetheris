"""Shared TTL cache over raw payloads (FR-007).

Spec §3.2 requires "local caching layer with configurable TTLs to prevent
hitting strict free-tier quotas", and spec §4 requires the ingestion step to
check the cache *before* calling a provider.

The cache is a thin TTL policy over the append-only :class:`RawPayloadStore`:
a cached payload that has not outlived its TTL is served and the provider is
never called. ``--force`` bypasses it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from src.ingestion.payloads import RawPayload, RawPayloadStore
from src.observability.metrics import get_metrics
from src.observability.telemetry import span_event


@dataclass(frozen=True)
class CacheOutcome:
    """Result of a cache lookup, carrying enough information for `status`."""

    hit: bool
    payload: RawPayload | None
    reason: str
    ttl_seconds: int | None = None


class TTLCache:
    """TTL cache whose source of truth is the raw payload store."""

    def __init__(self, store: RawPayloadStore, *, default_ttl_s: int = 3600) -> None:
        self._store = store
        self._default_ttl_s = default_ttl_s
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    def get(
        self,
        provider: str,
        endpoint: str,
        request_hash: str,
        *,
        ttl_override: int | None = None,
    ) -> CacheOutcome:
        """Look up a fresh-enough cached payload for this request.

        ``ttl_override`` replaces the stored TTL for this lookup only, which is
        how ``--force`` (an override of 0) bypasses the cache without needing a
        separate code path in the registry.
        """
        payload = self._store.latest(provider, endpoint, request_hash)
        if payload is None:
            self._record(hit=False)
            return CacheOutcome(False, None, "absent")

        effective_ttl = (
            payload.ttl_seconds if ttl_override is None else ttl_override
        )
        if effective_ttl is not None and self._is_stale(payload, effective_ttl):
            self._record(hit=False)
            return CacheOutcome(
                False, payload, "stale", ttl_seconds=effective_ttl
            )

        self._record(hit=True)
        return CacheOutcome(
            True, payload, "fresh", ttl_seconds=effective_ttl
        )

    @staticmethod
    def _is_stale(payload: RawPayload, ttl_seconds: int) -> bool:
        if ttl_seconds <= 0:
            return True  # a zero TTL makes every payload stale — this is --force
        now = datetime.now(UTC)
        return (now - payload.fetched_at).total_seconds() >= ttl_seconds

    def ttl_for(self, override: int | None = None) -> int:
        """Resolve the effective TTL: explicit override, else the configured default."""
        return self._default_ttl_s if override is None else override

    def record_store(self, provider: str) -> None:
        """Note that a payload was appended to the store.

        Called by the registry after a successful fetch so telemetry reflects a
        store even though the cache itself reads from the same store and needs
        no separate write path.
        """
        span_event("cache.stored", **{"aetheris.provider": provider})

    # -- statistics (back SC-003) ---------------------------------------
    def stats(self) -> dict[str, float]:
        with self._lock:
            total = self._hits + self._misses
            ratio = (self._hits / total) if total else 0.0
            return {
                "hits": float(self._hits),
                "misses": float(self._misses),
                "total": float(total),
                "hit_ratio": ratio,
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._hits = 0
            self._misses = 0

    def _record(self, *, hit: bool) -> None:
        m = get_metrics()
        if hit:
            m.cache_hits.add(1)
        else:
            m.cache_misses.add(1)
        with self._lock:
            if hit:
                self._hits += 1
            else:
                self._misses += 1
