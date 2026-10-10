"""OpenTelemetry instrumentation for Aetheris (FR-017).

Constitution §IV requires that *every* workflow execution, ingestion fetch,
sentiment model inference, and graph mutation emit distributed trace spans and
performance metrics using open-source telemetry standards. This module provides
the single tracer/meter/logger configuration the whole pipeline shares.

A no-op fallback is used when no OTLP collector is reachable, so the pipeline
runs fully offline (dry-run / CI) without a telemetry backend.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from src.config.settings import Settings

_TRACER_NAME = "aetheris"
_initialized = False
log = logging.getLogger(__name__)


def _build_resource(settings: Settings) -> Resource:
    return Resource.create(
        {
            "service.name": settings.service_name,
            "service.version": "0.1.0",
        }
    )


def init_telemetry(settings: Settings, *, force: bool = False) -> None:
    """Initialise global OTel providers exactly once per process.

    Uses an OTLP gRPC exporter when ``settings.otlp_endpoint`` is set, otherwise
    falls back to a console exporter so spans are still produced (and testable)
    without a collector.
    """
    global _initialized
    if _initialized and not force:
        return
    _initialized = True

    resource = _build_resource(settings)
    provider = TracerProvider(resource=resource)

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
            log.warning("otel: OTLP exporter unavailable (%s); using console", exc)
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        # No collector configured: still emit spans, just print them. Keeps the
        # "spans always emitted" contract true even in dry-run/CI.
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

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

    This is the only sanctioned way to create spans in Aetheris so that every
    layer emits them consistently (invariant in contracts/adapter-protocol.md).
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
