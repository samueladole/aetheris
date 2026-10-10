"""Ingestion adapter boundary and shared HTTP plumbing (T032).

This module defines the :class:`ProviderAdapter` protocol that every provider
adapter satisfies, plus the shared request/payload construction they all use.

The invariants encoded here come from `.specify/specs/contracts/adapter-protocol.md`
and are enforced mechanically by the import-linter contracts in `.importlinter`:

  A1  Adapters return **raw immutable payloads only** — no domain objects, no
      scoring, no graph writes.
  A2  Adapters never call another adapter; provider fallback is the registry's
      job.
  A3  Adapters contain **no** retry/backoff logic — that is the orchestration
      layer's policy. Retries are injected, not imported.
  A5  Adapters are importable without importing orchestration, sentiment, or
      graph.
  A7  A 429 or 5xx response raises a typed :class:`ProviderUnavailable`, never a
      swallowed error.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from src.ingestion.payloads import RawPayload, build_payload_id


class ProviderUnavailable(Exception):
    """A provider could not be served (rate-limited, exhausted, or down).

    Raised instead of swallowing the error so the registry can apply the
    configured fallback order (FR-020) and the failure can be recorded as a
    span event.
    """

    def __init__(self, provider: str, endpoint: str, status_code: int | None,
                 message: str) -> None:
        self.provider = provider
        self.endpoint = endpoint
        self.status_code = status_code
        super().__init__(
            f"{provider}::{endpoint} unavailable"
            + (f" (HTTP {status_code})" if status_code else "")
            + f": {message}"
        )

    @property
    def retryable(self) -> bool:
        """429 and 5xx are transient; 4xx (other than 429) are not."""
        if self.status_code is None:
            return True  # connection error / timeout — worth retrying
        return self.status_code == 429 or self.status_code >= 500


class AdapterError(RuntimeError):
    """An adapter failed to build or interpret a request (a bug, not a fault)."""


@dataclass(frozen=True)
class FetchRequest:
    """A provider request, normalised into cache and payload identity."""

    provider: str
    endpoint: str
    params: Mapping[str, str]
    url: str
    request_hash: str

    @classmethod
    def create(
        cls, provider: str, endpoint: str, url: str, params: Mapping[str, str]
    ) -> FetchRequest:
        """Build a request with a stable hash of everything that varies it."""
        canonical = "|".join(
            [provider, endpoint, url, *sorted(f"{k}={v}" for k, v in params.items())]
        )
        return cls(
            provider=provider,
            endpoint=endpoint,
            params=dict(params),
            url=url,
            request_hash=hashlib.sha256(canonical.encode()).hexdigest()[:16],
        )


@dataclass(frozen=True)
class HttpResponse:
    """A raw provider response, before any interpretation."""

    status_code: int
    body: bytes
    headers: Mapping[str, str]

    def raise_for_unavailable(self, provider: str, endpoint: str) -> None:
        if self.status_code == 429:
            raise ProviderUnavailable(
                provider, endpoint, 429, "free-tier quota exceeded"
            )
        if self.status_code >= 500:
            raise ProviderUnavailable(
                provider, endpoint, self.status_code, "provider server error"
            )
        if self.status_code == 401:
            raise ProviderUnavailable(
                provider, endpoint, 401, "missing or invalid API key"
            )
        if self.status_code >= 400:
            raise ProviderUnavailable(
                provider, endpoint, self.status_code, "request rejected"
            )


@runtime_checkable
class HttpClient(Protocol):
    """The minimal HTTP surface the adapters need.

    Declared as a Protocol so tests can substitute a cassette-backed fake and no
    live provider call is ever made in CI (invariant A6).
    """

    def get(
        self, url: str, *, params: Mapping[str, str], headers: Mapping[str, str],
        timeout: float,
    ) -> HttpResponse: ...


class RequestsHttpClient:
    """Production HTTP client over :mod:`requests`."""

    def __init__(self, session: Any = None) -> None:
        if session is None:
            import requests

            session = requests.Session()
        self._session = session

    def get(
        self, url: str, *, params: Mapping[str, str], headers: Mapping[str, str],
        timeout: float,
    ) -> HttpResponse:
        try:
            resp = self._session.get(
                url, params=dict(params), headers=dict(headers), timeout=timeout
            )
        except Exception as exc:  # connection error, DNS, timeout
            raise ProviderUnavailable("?", "", None, str(exc)) from exc
        return HttpResponse(
            status_code=resp.status_code,
            body=resp.content,
            headers=dict(resp.headers),
        )


@runtime_checkable
class ProviderAdapter(Protocol):
    """The ingestion boundary every adapter satisfies (A1–A7).

    Implementations are thin: build a request, hand it to the shared HTTP client,
    and wrap the response into a raw payload. No retries (A3), no cross-adapter
    calls (A2), no interpretation beyond provider-specific parameter shaping.
    """

    provider: str

    def can_handle(self, endpoint: str) -> bool: ...

    def build_request(
        self, endpoint: str, params: Mapping[str, str]
    ) -> FetchRequest: ...

    def headers(self) -> Mapping[str, str]: ...


def to_raw_payload(request: FetchRequest, response: HttpResponse,
                   ttl_seconds: int | None) -> RawPayload:
    """Wrap a provider response into a raw immutable payload (A1)."""
    fetched_at = datetime.now(UTC)
    return RawPayload(
        payload_id=build_payload_id(
            request.provider, request.endpoint, request.request_hash, fetched_at
        ),
        provider=request.provider,
        endpoint=request.endpoint,
        request_hash=request.request_hash,
        schema_version=1,
        body=response.body,
        fetched_at=fetched_at,
        ttl_seconds=ttl_seconds,
    )


__all__ = [
    "AdapterError",
    "FetchRequest",
    "HttpClient",
    "HttpResponse",
    "ProviderAdapter",
    "ProviderUnavailable",
    "RequestsHttpClient",
    "to_raw_payload",
]
