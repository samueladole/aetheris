"""Pipeline step functions: ingest, score, integrate into the graph, evaluate.

These are plain functions with JSON-serialisable results, so the durable
workflow can checkpoint each step's output and the CLI can run the same code
on stored data. Nothing here imports DBOS.

Ingestion returns a *manifest*: one entry per fetched raw payload, keyed by
kind (``macro``, ``prices``, ``profile``, ``news``) and by the series id,
ticker, or news category it covers. The later steps read payload bodies back
from the raw store by ``payload_id`` rather than passing bodies around.

Every timestamp written to the graph comes from the caller's ``at``, never the
wall clock, so replaying a run with the same ``at`` reproduces the same graph.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from src.config.watchlist import WatchedAsset, Watchlist
from src.graph.align import Observation
from src.graph.edges import (
    close_available_at,
    correlation_edges,
    influence_edges,
    release_available_at,
)
from src.graph.queries import sentiment_scores
from src.graph.repository import GraphRepository
from src.graph.schema import (
    Asset,
    AssetClass,
    Frequency,
    GraphDatabase,
    GraphEdge,
    MacroIndicator,
    NodeType,
    RelationType,
    SentimentMetric,
)
from src.ingestion.base import ProviderUnavailable
from src.ingestion.parsers import (
    AssetProfile,
    MacroPoint,
    fred_window_start,
    parse_finnhub_news,
    parse_finnhub_profile,
    parse_fred_observations,
    parse_tiingo_prices,
)
from src.ingestion.payloads import RawPayload, RawPayloadStore
from src.ingestion.registry import IngestionRegistry
from src.observability.evaluate import compute_drift_report
from src.sentiment.analyzer import TextTooLongError, score_text
from src.sentiment.entities import WatchTerms, resolve_entities
from src.sentiment.schema import (
    Calibration,
    Provenance,
    ValidationError,
    build_payload,
    text_hash,
)

MACRO = "macro"
PRICES = "prices"
PROFILE = "profile"
NEWS = "news"

NEWS_CATEGORY = "general"
PROFILE_TTL_S = 7 * 86400
NEWS_TTL_S = 900

# Where each manifest kind comes from: (data type, provider, endpoint). Each
# endpoint is declared by exactly one provider, so the provider is fixed.
_SOURCES: dict[str, tuple[str, str, str]] = {
    MACRO: ("macro", "fred", "series/observations"),
    PRICES: ("prices", "tiingo", "tiingo/daily"),
    PROFILE: ("fundamentals", "finnhub", "stock/profile2"),
    NEWS: ("news", "finnhub", "/news"),
}



def pipeline_providers() -> tuple[str, ...]:
    """The providers the pipeline fetches from, sorted; a live run needs their keys."""
    return tuple(sorted({provider for _type, provider, _endpoint in _SOURCES.values()}))


# The request parameter that holds each kind's manifest key.
_KEY_PARAMS: dict[str, str] = {
    MACRO: "series_id",
    PRICES: "tickers",
    PROFILE: "symbol",
    NEWS: "category",
}

# A transport error can quote the request URL, credentials included, and a
# failure message is checkpointed and printed, so credentials are masked.
_CREDENTIAL = re.compile(r"\b(api_key|apikey|token)=[^&\s'\"]+", re.IGNORECASE)


class IngestError(RuntimeError):
    """Raised when ingestion fetched nothing, so there is nothing to score."""


def _utc(value: datetime, name: str) -> datetime:
    """Return ``value`` in UTC, refusing a naive time (VR-5)."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"{name} is timezone-naive. The no-lookahead invariant (VR-5) cannot "
            "be enforced without an unambiguous clock."
        )
    return value.astimezone(UTC)


def _redact(message: str) -> str:
    return _CREDENTIAL.sub(lambda m: f"{m.group(1)}=***", message)


def _requests(
    watchlist: Watchlist, now: datetime
) -> list[tuple[str, str, dict[str, str], int | None]]:
    """Every fetch the watchlist needs, as ``(kind, key, params, ttl_seconds)``.

    The order is fixed (series, prices, profiles, news) so manifests compare
    equal whichever way they were built.

    Macro fetches ask FRED for first releases from the start of the longest
    derivation window only. Over the full history a daily series such as DGS10
    exceeds FRED's vintage-date limit and the request is refused (HTTP 400).
    """
    start = (now - timedelta(days=watchlist.price_history_days)).date().isoformat()
    window = max(watchlist.influence_window_days, watchlist.price_history_days)
    macro_start = (now - timedelta(days=window)).date().isoformat()
    requests: list[tuple[str, str, dict[str, str], int | None]] = [
        (MACRO, s.series_id, {"series_id": s.series_id, "realtime_start": macro_start}, None)
        for s in watchlist.macro
    ]
    requests += [
        (PRICES, a.ticker, {"tickers": a.ticker, "startDate": start}, None)
        for a in watchlist.assets
    ]
    requests += [(PROFILE, a.ticker, {"symbol": a.ticker}, PROFILE_TTL_S) for a in watchlist.assets]
    requests.append((NEWS, NEWS_CATEGORY, {"category": NEWS_CATEGORY}, NEWS_TTL_S))
    return requests


def run_ingest(registry: IngestionRegistry, watchlist: Watchlist, *, now: datetime) -> dict:
    """Fetch everything the watchlist needs and return the manifest.

    A fetch whose provider is unavailable is recorded in ``failures`` and the
    rest continue. Any other exception is a defect and propagates.

    Args:
        registry: A registry with adapters registered for every provider.
        watchlist: The series and assets to fetch.
        now: The run time; prices are requested from ``now - price_history_days``
            and macro first releases from ``now`` minus the longer of the
            influence and price-history windows.

    Returns:
        ``{"at": iso, "entries": [{"kind", "key", "payload_id"}],
        "failures": [{"kind", "key", "error"}]}``.

    Raises:
        IngestError: When every fetch failed.
    """
    now = _utc(now, "now")
    entries: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    for kind, key, params, ttl in _requests(watchlist, now):
        data_type, _provider, endpoint = _SOURCES[kind]
        try:
            outcome = registry.fetch(
                data_type, endpoint=endpoint, params=params, ttl_seconds=ttl
            )
        except ProviderUnavailable as exc:
            failures.append({"kind": kind, "key": key, "error": _redact(str(exc))})
            continue
        entries.append({"kind": kind, "key": key, "payload_id": outcome.payload.payload_id})

    if not entries:
        summary = "; ".join(f"{f['kind']} {f['key']}: {f['error']}" for f in failures)
        raise IngestError(f"every fetch failed ({len(failures)}): {summary}")
    return {"at": now.isoformat(), "entries": entries, "failures": failures}


def manifest_from_store(store: RawPayloadStore, watchlist: Watchlist, *, at: datetime) -> dict:
    """Rebuild the manifest from payloads already in the raw store.

    For each watched item the newest stored payload is used, matched on the
    request parameters the fetch recorded. A watched item with no stored
    payload is listed in ``failures``. Payloads are not filtered by ``at``,
    which only stamps the manifest.

    Args:
        store: The raw payload store.
        watchlist: The series and assets to look for.
        at: The time the manifest is built for.

    Returns:
        The same shape as :func:`run_ingest`'s result.
    """
    at = _utc(at, "at")
    newest: dict[tuple[str, str], str] = {}
    for kind, (_data_type, provider, endpoint) in _SOURCES.items():
        # latest_by_endpoint is newest first, so the first payload per key wins.
        for payload in store.latest_by_endpoint(provider, endpoint):
            key = (payload.request_params or {}).get(_KEY_PARAMS[kind])
            if key is not None:
                newest.setdefault((kind, key), payload.payload_id)

    entries: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    for kind, key, _params, _ttl in _requests(watchlist, at):
        payload_id = newest.get((kind, key))
        if payload_id is None:
            failures.append({"kind": kind, "key": key, "error": "no stored payload"})
        else:
            entries.append({"kind": kind, "key": key, "payload_id": payload_id})
    return {"at": at.isoformat(), "entries": entries, "failures": failures}


def _entries(manifest: Mapping[str, Any], kind: str) -> list[dict[str, str]]:
    return [e for e in manifest["entries"] if e["kind"] == kind]


def _payload(store: RawPayloadStore, payload_id: str) -> RawPayload:
    payload: RawPayload | None = store.get(payload_id)
    if payload is None:
        raise LookupError(f"raw payload {payload_id!r} is not in the store")
    return payload


def _body(store: RawPayloadStore, payload_id: str) -> bytes:
    return _payload(store, payload_id).body


def _fred_points(payload: RawPayload) -> list[MacroPoint]:
    """First releases in the payload, without rows clipped to the window start.

    The window start is the ``realtime_start`` the fetch requested, else the one
    FRED echoed in the body (an ad-hoc ``aetheris ingest`` stores only the
    caller's parameters, not the adapter's default).
    """
    requested = (payload.request_params or {}).get("realtime_start")
    window_start = (
        date.fromisoformat(requested) if requested else fred_window_start(payload.body)
    )
    return parse_fred_observations(payload.body, window_start=window_start)


def _profiles(
    store: RawPayloadStore, manifest: Mapping[str, Any]
) -> dict[str, AssetProfile | None]:
    """Parsed profiles by ticker; ``None`` where Finnhub knows no such symbol."""
    return {
        e["key"]: parse_finnhub_profile(_body(store, e["payload_id"]))
        for e in _entries(manifest, PROFILE)
    }


def build_watch_terms(
    watchlist: Watchlist, profiles: Mapping[str, AssetProfile | None]
) -> list[WatchTerms]:
    """Combine each asset's configured names with its profile name.

    Blank names are dropped, because an empty pattern matches almost any text.
    For the same reason an asset whose ticker is blank gets no terms at all.

    Args:
        watchlist: The watched assets.
        profiles: Parsed profiles keyed by ticker, ``None`` for unknown symbols.

    Returns:
        One ``WatchTerms`` per asset with a usable ticker, in watchlist order.
    """
    terms: list[WatchTerms] = []
    for asset in watchlist.assets:
        ticker = asset.ticker.strip()
        if not ticker:
            continue
        profile = profiles.get(asset.ticker)
        candidates = [*asset.names, profile.name if profile is not None else ""]
        names = tuple(dict.fromkeys(n.strip() for n in candidates if n.strip()))
        terms.append(WatchTerms(asset.asset_id, ticker, names))
    return terms


def run_sentiment(
    store: RawPayloadStore,
    manifest: Mapping[str, Any],
    watchlist: Watchlist,
    *,
    model_id: str = "stub",
    temperature: float = 1.0,
) -> dict:
    """Score every news article that mentions a watched asset.

    The scored text is ``headline + ". " + summary``. An article is scored once
    and yields one payload per matched asset. Articles repeated across news
    payloads are counted and scored once. An article published after the
    manifest's ``at`` is counted in ``after_at`` and not scored (no lookahead).

    Args:
        store: The raw payload store.
        manifest: The ingestion manifest.
        watchlist: The assets to match.
        model_id: Sentiment model from the permitted manifest.
        temperature: Calibration temperature (VR-2).

    Returns:
        ``{"scored": [{"payload", "match_weight"}], "articles": int,
        "after_at": int, "unmatched": int, "rejected": int}``. ``articles``
        counts every distinct article read, ``after_at`` those skipped for
        being published after ``at``, and ``rejected`` the payloads not
        produced because validation failed or the analyzer refused the text
        (over its model's context limit).
    """
    at = _utc(datetime.fromisoformat(manifest["at"]), "manifest at")
    terms = build_watch_terms(watchlist, _profiles(store, manifest))
    calibration = Calibration(
        method="temperature_scaling", temperature=temperature, calibrated_on="pipeline"
    )
    scored: list[dict[str, Any]] = []
    articles = after_at = unmatched = rejected = 0
    seen: set[str] = set()
    for entry in _entries(manifest, NEWS):
        for item in parse_finnhub_news(_body(store, entry["payload_id"])):
            if item.article_id in seen:
                continue
            seen.add(item.article_id)
            articles += 1
            if item.published_at > at:
                after_at += 1
                continue
            headline, summary = item.headline or "", item.summary or ""
            matches = resolve_entities(headline, summary, terms)
            if not matches:
                unmatched += 1
                continue
            text = f"{headline}. {summary}"
            try:
                polarity, _probabilities, model = score_text(
                    text, model_id=model_id, temperature=temperature
                )
            except TextTooLongError:
                # Over the model's context limit: no payload for any match.
                rejected += len(matches)
                continue
            provenance = Provenance(
                payload_id=entry["payload_id"],
                text_hash=text_hash(text),
                text_length_chars=len(text),
            )
            for match in matches:
                try:
                    payload = build_payload(
                        entity_id=match.entity_id,
                        polarity_score=float(polarity),
                        confidence=polarity.confidence,
                        timestamp=item.published_at,
                        source_reference=f"finnhub:news:{item.article_id}",
                        model=model,
                        calibration=calibration,
                        provenance=provenance,
                        text=text,
                    )
                except ValidationError:
                    rejected += 1
                    continue
                scored.append({"payload": payload, "match_weight": match.weight})
    return {
        "scored": scored,
        "articles": articles,
        "after_at": after_at,
        "unmatched": unmatched,
        "rejected": rejected,
    }


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=UTC)


def _price_observations(
    store: RawPayloadStore, manifest: Mapping[str, Any], watchlist: Watchlist
) -> dict[str, list[Observation]]:
    """Adjusted closes by asset id, each available at that day's New York close."""
    asset_ids = {a.ticker: a.asset_id for a in watchlist.assets}
    prices: dict[str, list[Observation]] = {}
    for entry in _entries(manifest, PRICES):
        asset_id = asset_ids.get(entry["key"])
        if asset_id is None:
            continue
        prices[asset_id] = [
            Observation(
                value=point.adj_close,
                observed_at=_midnight(point.trading_day),
                available_at=close_available_at(point.trading_day),
            )
            for point in parse_tiingo_prices(_body(store, entry["payload_id"]))
        ]
    return prices


def _macro_observations(
    store: RawPayloadStore, manifest: Mapping[str, Any], watchlist: Watchlist
) -> dict[str, list[Observation]]:
    """First-release values by indicator code, each available at its release time.

    Rows FRED clipped to the requested window start are dropped (see
    :func:`_fred_points`): their first-release date is unknown.
    """
    series_by_id = {s.series_id: s for s in watchlist.macro}
    macro: dict[str, list[Observation]] = {}
    for entry in _entries(manifest, MACRO):
        series = series_by_id.get(entry["key"])
        if series is None:
            continue
        macro[series.indicator_code] = [
            Observation(
                value=point.value,
                observed_at=_midnight(point.observed_on),
                available_at=release_available_at(point.released_on, series.release_time),
            )
            for point in _fred_points(_payload(store, entry["payload_id"]))
        ]
    return macro


def _asset_node(asset: WatchedAsset, profile: AssetProfile | None, at: datetime) -> Asset:
    """The Asset node: profile name and currency where known, else the watchlist's."""
    asset_class, exchange, _symbol = asset.asset_id.split(":", 2)
    name = profile.name.strip() if profile is not None else ""
    currency = profile.currency.strip() if profile is not None else ""
    fallback_name = asset.names[0] if asset.names else asset.ticker
    return Asset(
        asset_id=asset.asset_id,
        ticker=asset.ticker,
        name=name or fallback_name,
        asset_class=AssetClass(asset_class),
        exchange=exchange,
        currency=currency or "USD",
        updated_at=at,
    )


def _metric(payload: Mapping[str, Any], at: datetime) -> SentimentMetric:
    entity_id = payload["entity_id"]
    return SentimentMetric(
        metric_id=payload["metric_id"],
        entity_id=entity_id,
        entity_type=(
            NodeType.MACRO_INDICATOR if entity_id.startswith("macro:") else NodeType.ASSET
        ),
        score=payload["polarity_score"],
        confidence=payload["confidence"],
        timestamp=datetime.fromisoformat(payload["timestamp"]),
        source_reference=payload["source_reference"],
        model_id=f"{payload['model']['id']}@{payload['model']['revision']}",
        text_hash=payload["provenance"]["text_hash"],
        created_at=at,
    )


def run_graph_integration(
    store: RawPayloadStore,
    repo: GraphRepository,
    manifest: Mapping[str, Any],
    sentiment: Mapping[str, Any],
    watchlist: Watchlist,
    *,
    at: datetime,
) -> dict:
    """Align the run's data and upsert it into the knowledge graph.

    Writes the watched assets and indicators, every parsed FRED value as a
    ``macro_observation``, one SentimentMetric with its observation and lineage
    rows per scored item, a TARGETS edge from each metric to its asset, and the
    CORRELATES_WITH and INFLUENCES edges derived as of ``at``. Every write is
    idempotent, so re-running the step changes nothing.

    Args:
        store: The raw payload store.
        repo: The graph repository to write.
        manifest: The ingestion manifest.
        sentiment: :func:`run_sentiment`'s result.
        watchlist: The watched assets and series.
        at: The as-of time; also every timestamp this step writes.

    Returns:
        ``{"assets", "indicators", "metrics", "edges": {"TARGETS",
        "CORRELATES_WITH", "INFLUENCES"}}`` counts.
    """
    at = _utc(at, "at")
    profiles = _profiles(store, manifest)
    for asset in watchlist.assets:
        repo.upsert_asset(_asset_node(asset, profiles.get(asset.ticker), at))
    for series in watchlist.macro:
        repo.upsert_macro_indicator(
            MacroIndicator(
                indicator_code=series.indicator_code,
                name=series.name,
                unit=series.unit,
                frequency=Frequency(series.frequency),
                seasonal_adjustment=series.seasonal_adjustment,
                provider="fred",
                updated_at=at,
            )
        )

    macro = _macro_observations(store, manifest, watchlist)
    for code, observations in macro.items():
        for observation in observations:
            repo.upsert_macro_observation(code, observation)

    targets: dict[str, GraphEdge] = {}
    for item in sentiment["scored"]:
        payload = item["payload"]
        metric = _metric(payload, at)
        repo.upsert_sentiment_metric(metric)
        repo.record_sentiment_provenance(
            metric.metric_id,
            payload_id=payload["provenance"]["payload_id"],
            text_hash=metric.text_hash,
            model_id=metric.model_id,
            observed_at=metric.timestamp,
            available_at=metric.timestamp,
        )
        targets[metric.metric_id] = GraphEdge(
            src_node=metric.metric_id,
            dst_node=metric.entity_id,
            rel_type=RelationType.TARGETS,
            weight=item["match_weight"],
            confidence=item["match_weight"],
            valid_from=metric.timestamp,
            observation_count=1,
            computed_at=at,
        )

    prices = _price_observations(store, manifest, watchlist)
    correlations = correlation_edges(
        prices, at=at, window_days=watchlist.correlation_window_days
    )
    influences = influence_edges(
        macro, prices, at=at, window_days=watchlist.influence_window_days
    )
    repo.upsert_edges([*targets.values(), *correlations, *influences])
    return {
        "assets": len(watchlist.assets),
        "indicators": len(watchlist.macro),
        "metrics": len(targets),
        "edges": {
            RelationType.TARGETS.value: len(targets),
            RelationType.CORRELATES_WITH.value: len(correlations),
            RelationType.INFLUENCES.value: len(influences),
        },
    }


def run_evaluation(
    db: GraphDatabase,
    current: Sequence[Mapping[str, Any]],
    *,
    at: datetime,
    reference_days: int = 7,
    drift_threshold: float = 0.1,
    entity_id: str | None = None,
    min_confidence: float = 0.0,
) -> dict:
    """Evaluate the current scores and their drift from the graph's recent history.

    The reference is every score in ``[at - reference_days, at)`` except the
    current items' own metrics. With no reference scores ``drift_score`` is
    ``None``, because there is nothing to drift from.

    ``entity_id`` and ``min_confidence`` apply to the reference only; a caller
    that filters ``current`` passes the same filters, so the scores it dropped
    are not compared against the ones it kept.

    Args:
        db: The graph database to read history from.
        current: Items with ``metric_id``, ``entity_id``, ``polarity_score``,
            and ``confidence``, such as sentiment payloads.
        at: End of the reference window (exclusive).
        reference_days: Length of the reference window in days.
        drift_threshold: Mean polarity shift that counts as drift.
        entity_id: Evaluate one entity: the reference keeps only its scores and
            the evaluation is labelled with it. ``None`` means every entity.
        min_confidence: The reference keeps only scores at or above this
            confidence.

    Returns:
        ``{"evaluation": Evaluation.as_dict(), "mlflow": status}``.
    """
    at = _utc(at, "at")
    current_ids = {item["metric_id"] for item in current}
    history = sentiment_scores(db, start=at - timedelta(days=reference_days), end=at)
    reference = [
        row["score"]
        for row in history
        if row["metric_id"] not in current_ids
        and (entity_id is None or row["entity_id"] == entity_id)
        and row["confidence"] >= min_confidence
    ]
    evaluation, status = compute_drift_report(
        [dict(item) for item in current],
        reference=reference or None,
        drift_threshold=drift_threshold,
        entity_id=entity_id,
    )
    return {"evaluation": evaluation.as_dict(), "mlflow": status}


__all__ = [
    "IngestError",
    "build_watch_terms",
    "manifest_from_store",
    "pipeline_providers",
    "run_evaluation",
    "run_graph_integration",
    "run_ingest",
    "run_sentiment",
]
