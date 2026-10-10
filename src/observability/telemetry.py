"""OpenTelemetry instrumentation for Aetheris (FR-017).

Constitution §IV requires that *every* workflow execution, ingestion fetch,
sentiment model inference, and graph mutation emit distributed trace spans and
performance metrics using open-source telemetry standards. This module provides
the single tracer/meter/logger configuration the whole pipeline shares.

Export behaviour:

  * ``OTEL_EXPORTER_OTLP_ENDPOINT`` set  -> export to that collector (self-hosted
    Jaeger per research.md §C).
  * otherwise                            -> no network exporter is added. Spans
    are still created and *still recording*; they simply have nowhere to be
    shipped. An in-memory exporter records them so tests can assert on span
    coverage (T067) without a collector.

A console exporter is deliberately NOT installed by default: it captures stdout
at construction time and emits noise into any caller's output, which breaks CLI
JSON output and pytest capture. Set ``AETHERIS_TRACE_STDOUT=1`` to opt in.
"""

from __future__ import annotations

import logging
import os
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from src.config.settings import Settings

_TRACER_NAME = "aetheris"
_initialized = False

log = logging.getLogger(__name__)


class InMemorySpanExporter:
    """Keeps the most recent spans in memory, for tests and `status`.

    Bounded so a long-running process cannot accumulate spans unboundedly.
    """

    MAX_SPANS = 2000

    def __init__(self) -> None:
        self._spans: deque[ReadableSpan] = deque(maxlen=self.MAX_SPANS)

    def export(self, spans: list[ReadableSpan]) -> None:
        self._spans.extend(spans)

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def snapshot(self) -> list[ReadableSpan]:
        return list(self._spans)

    def names(self) -> list[str]:
        return [s.name for s in self._spans]

    def clear(self) -> None:
        self._spans.clear()


# Process-wide, so `aetheris status` and tests can both read it.
_memory_exporter = InMemorySpanExporter()


def get_memory_exporter() -> InMemorySpanExporter:
    """The in-memory span store, useful for assertions and diagnostics."""
    return _memory_exporter


def _build_resource(settings: Settings) -> Resource:
    return Resource.create(
        {
            "service.name": settings.service_name,
            "service.version": "0.1.0",
        }
    )


def init_telemetry(settings: Settings, *, force: bool = False) -> None:
    """Initialise global OTel providers exactly once per process."""
    global _initialized
    if _initialized and not force:
        return
    _initialized = True

    resource = _build_resource(settings)
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(_memory_exporter))

    if settings.otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_endpoint))
            )
            log.info("otel: exporting spans to %s", settings.otlp_endpoint)
        except Exception as exc:  # pragma: no cover - transport failure
            log.warning("otel: OTLP exporter unavailable (%s)", exc)

    if os.environ.get("AETHERIS_TRACE_STDOUT", "").strip().lower() in {"1", "true", "yes"}:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)

    # Meter provider is lazily created by the OTel API; metrics.py reads from it.
    from opentelemetry.sdk.metrics import MeterProvider

    metrics.set_meter_provider(MeterProvider(resource=resource))


def get_tracer() -> trace.Tracer:
    """Return the shared tracer. Safe to call before :func:`init_telemetry`."""
    return trace.get_tracer(_TRACER_NAME)


def get_meter() -> metrics.Meter:
    """Return the shared meter."""
    return metrics.get_meter(_TRACER_NAME)


@contextmanager
def span(
    name: str, *, kind: trace.SpanKind = trace.SpanKind.INTERNAL, **attributes: Any
) -> Iterator[trace.Span]:
    """Start a span, recording any exception as an error event before re-raising.

    This is the only sanctioned way to create spans in Aetheris, so every layer
    emits them consistently (adapter-protocol.md).
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(name, kind=kind, attributes=attributes) as s:
        try:
            yield s
        except Exception as exc:
            s.record_exception(exc)
            s.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            raise


def span_event(name: str, **attributes: Any) -> None:
    """Record an event on the current span, if one is active."""
    current = trace.get_current_span()
    if current is None or not current.is_recording():
        return
    current.add_event(name, attributes=attributes)


def annotate(**attributes: Any) -> None:
    """Add attributes to the current span, if one is active."""
    current = trace.get_current_span()
    if current is None or not current.is_recording():
        return
    for k, v in attributes.items():
        current.set_attribute(k, v)
