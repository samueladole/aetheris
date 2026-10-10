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

from src.ingestion.payloads import RawPayload, RawPayloadStore
from src.observability.metrics import get_metrics


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

    def get(self, provider: str, endpoint: str, request_hash: str) -> CacheOutcome:
        """Look up a fresh-enough cached payload for this request."""
        payload = self._store.latest(provider, endpoint, request_hash)
        if payload is None:
            self._record(hit=False)
            return CacheOutcome(False, None, "absent")

        if payload.is_stale():
            self._record(hit=False)
            return CacheOutcome(
                False, payload, "stale", ttl_seconds=payload.ttl_seconds
            )

        self._record(hit=True)
        return CacheOutcome(True, payload, "fresh", ttl_seconds=payload.ttl_seconds)

    def ttl_for(self, override: int | None = None) -> int:
        """Resolve the effective TTL: explicit override, else the configured default."""
        return self._default_ttl_s if override is None else override

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
