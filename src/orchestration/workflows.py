"""Durable workflow definitions (FR-001, FR-016, FR-017).

Registers the five-step pipeline from spec §4 as a durable DBOS workflow:

    1. Trigger
    2. Ingestion          -> raw payloads
    3. Sentiment scoring  -> SentimentMetric payloads
    4. Graph integration  -> backward as-of join, then upsert
    5. Evaluation         -> telemetry + drift

Every step is a durable ``@DBOS.step``, so each one is checkpointed
independently (FR-002) and retried with the configured bounded backoff
(FR-003). A step that is finally exhausted marks the workflow failed and its
compensating action runs (compensation.py).

Steps are declared ``async`` by necessity, not preference: DBOS 3.x only
supports ``timeout_seconds`` on async steps, and the activity timeout is
required by spec §3.1 / FR-003. Each step body runs the matching function in
``src.orchestration.steps`` in a worker thread: that work blocks (HTTP,
SQLite, DuckDB), and on the event loop it would keep the timeout from firing.

Each step opens the raw store and the graph database it needs from
``settings`` and closes them before it returns. Only the checkpointed outputs
(the ingestion manifest, the scored items, the counts) pass between steps.

The trigger step fixes the run's as-of time ``at``. Every later step uses that
checkpointed value rather than the clock, so a resumed run writes the same
timestamps as an uninterrupted one. With ``dry_run`` set, the data steps return
zero results and contact no provider.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from src.config.settings import Settings, get_settings
from src.config.watchlist import Watchlist, load_watchlist
from src.graph.repository import GraphRepository
from src.graph.schema import GraphDatabase
from src.ingestion.base import HttpClient
from src.ingestion.payloads import RawPayloadStore
from src.observability.metrics import get_metrics
from src.observability.telemetry import annotate, span
from src.orchestration import checkpoints, steps
from src.orchestration.checkpoints import WorkflowHandle
from src.orchestration.compensation import (
    CompensationRegistry,
    StepOutcome,
    StepStateMachine,
)
from src.orchestration.retry import build_step_retry_policy, step_retry_kwargs_from_policy
from src.orchestration.wiring import build_ingestion_registry

# The five steps of spec §4, in order.
STEP_INGEST = "ingest"
STEP_SENTIMENT = "sentiment"
STEP_GRAPH = "graph_integration"
STEP_EVALUATION = "evaluation"
STEP_TRIGGER = "trigger"

PIPELINE_STEPS = (
    STEP_TRIGGER,
    STEP_INGEST,
    STEP_SENTIMENT,
    STEP_GRAPH,
    STEP_EVALUATION,
)

WORKFLOW_NAME = "aetheris_pipeline"


class WorkflowError(RuntimeError):
    """Raised when the pipeline workflow cannot be started."""


def build_registry() -> CompensationRegistry:
    """Compensators for each fallible step (data-model.md §5).

    Ingestion and sentiment are additive and cheap to redo, so they compensate by
    discarding the partial work rather than undoing committed state — the
    immutable payload store means nothing actually needs deleting.
    """
    registry = CompensationRegistry()

    def _discard_partial(name: str) -> Callable[[], None]:
        def action() -> None:
            # Raw payloads are append-only (VR-8); a partial fetch simply
            # remains in history and is superseded by the next attempt.
            span("compensate.discard", **{"aetheris.step": name})

        return action

    def _noop(name: str) -> Callable[[], None]:
        """Explicit no-op compensator: documents "nothing to undo"."""

        def action() -> None:
            span("compensate.noop", **{"aetheris.step": name})

        return action

    def _undo_graph() -> None:
        # Graph upserts are idempotent by (src, dst, rel_type, valid_from); a
        # failed window is simply not written, so the next run retries cleanly.
        span("compensate.graph")

    registry.register(STEP_TRIGGER, _noop(STEP_TRIGGER))
    registry.register(STEP_INGEST, _discard_partial(STEP_INGEST))
    registry.register(STEP_SENTIMENT, _discard_partial(STEP_SENTIMENT))
    registry.register(STEP_GRAPH, _undo_graph)
    registry.register(STEP_EVALUATION, _discard_partial(STEP_EVALUATION))
    return registry


def _summary(
    dry_run: bool,
    manifest: Mapping[str, Any],
    sentiment: Mapping[str, Any],
    graph: Mapping[str, Any],
) -> dict[str, Any]:
    """What the run fetched, scored, and linked; all zero for a dry run."""
    if dry_run:
        return {"fetched": 0, "scored": 0, "edges": {}}
    return {
        "fetched": len(manifest["entries"]),
        "scored": len(sentiment["scored"]),
        "edges": dict(graph["edges"]),
    }


def register_workflow(
    settings: Settings,
    *,
    http: HttpClient | None = None,
    watchlist: Watchlist | None = None,
) -> None:
    """Register the pipeline workflow with the DBOS runtime.

    DBOS is imported lazily so this module can be imported without a DBOS
    runtime being configured (Principle II: layers stay importable in
    isolation).

    Args:
        settings: Supplies the store paths, credentials, retry policy, and
            ``dry_run``.
        http: HTTP client for the providers; tests pass a fake so no live call
            is made. Defaults to a real client.
        watchlist: What to fetch and score. Defaults to the ``[watchlist]``
            table of the config file, else the built-in watchlist.
    """
    import dbos

    policy = build_step_retry_policy(settings)
    step_kwargs = step_retry_kwargs_from_policy(policy)
    watched = watchlist if watchlist is not None else load_watchlist(settings.config_path)

    # The blocking bodies of the data steps. Each runs in one worker thread and
    # closes what it opened there; RawPayloadStore connections are per thread.
    # They call through the `steps` module so tests can patch a step function.

    def _ingest(at: str) -> dict[str, Any]:
        registry = build_ingestion_registry(settings, http=http)
        try:
            return steps.run_ingest(registry, watched, now=datetime.fromisoformat(at))
        finally:
            if registry.payload_store is not None:
                registry.payload_store.close()

    def _sentiment(manifest: dict[str, Any]) -> dict[str, Any]:
        with closing(RawPayloadStore(settings.raw_db_path)) as store:
            return steps.run_sentiment(store, manifest, watched)

    def _graph(
        manifest: dict[str, Any], scored: dict[str, Any], at: str
    ) -> dict[str, Any]:
        with (
            closing(RawPayloadStore(settings.raw_db_path)) as store,
            closing(GraphDatabase(settings.db_path)) as db,
        ):
            return steps.run_graph_integration(
                store,
                GraphRepository(db),
                manifest,
                scored,
                watched,
                at=datetime.fromisoformat(at),
            )

    def _evaluate(scored: dict[str, Any], at: str) -> dict[str, Any]:
        current = [item["payload"] for item in scored["scored"]]
        with closing(GraphDatabase(settings.db_path)) as db:
            return steps.run_evaluation(db, current, at=datetime.fromisoformat(at))

    @dbos.DBOS.step(name=STEP_TRIGGER, **step_kwargs)
    async def trigger() -> dict[str, Any]:
        """Step 1: resolve the trigger, fix the run's ``at``, report the parameters."""
        with span("pipeline.trigger", **{"aetheris.step": STEP_TRIGGER}):
            return {
                "trigger": "schedule",
                "dry_run": settings.dry_run,
                # The only clock read in the run; checkpointed, so a resume reuses it.
                "at": datetime.now(UTC).isoformat(),
                "retry": {
                    "base_delay_ms": policy.base_delay_ms,
                    "multiplier": policy.multiplier,
                    "max_attempts": policy.max_attempts,
                    "jitter": policy.jitter,
                },
            }

    @dbos.DBOS.step(name=STEP_INGEST, **step_kwargs)
    async def ingest(dry_run: bool, at: str) -> dict[str, Any]:
        """Step 2: fetch the watchlist's raw payloads; returns the manifest."""
        get_metrics().fetch_total.add(1)
        with span("pipeline.ingest", **{"aetheris.step": STEP_INGEST}):
            if dry_run:
                return {"fetched": 0, "dry_run": True}
            return await asyncio.to_thread(_ingest, at)

    @dbos.DBOS.step(name=STEP_SENTIMENT, **step_kwargs)
    async def sentiment(dry_run: bool, manifest: dict[str, Any]) -> dict[str, Any]:
        """Step 3: score the ingested news that mentions a watched asset."""
        with span("pipeline.sentiment", **{"aetheris.step": STEP_SENTIMENT}):
            if dry_run:
                return {"scored": 0, "dry_run": True}
            return await asyncio.to_thread(_sentiment, manifest)

    @dbos.DBOS.step(name=STEP_GRAPH, **step_kwargs)
    async def graph_integration(
        dry_run: bool, manifest: dict[str, Any], scored: dict[str, Any], at: str
    ) -> dict[str, Any]:
        """Step 4: backward as-of align, then upsert into the knowledge graph."""
        get_metrics().graph_upserts.add(1)
        with span("pipeline.graph", **{"aetheris.step": STEP_GRAPH}):
            if dry_run:
                return {"upserted": 0, "dry_run": True}
            return await asyncio.to_thread(_graph, manifest, scored, at)

    @dbos.DBOS.step(name=STEP_EVALUATION, **step_kwargs)
    async def evaluate(dry_run: bool, scored: dict[str, Any], at: str) -> dict[str, Any]:
        """Step 5: validate telemetry and output consistency (FR-019)."""
        with span("pipeline.evaluate", **{"aetheris.step": STEP_EVALUATION}):
            if dry_run:
                return {"evaluated": 0, "dry_run": True}
            return await asyncio.to_thread(_evaluate, scored, at)

    registry = build_registry()

    @dbos.DBOS.workflow(name=WORKFLOW_NAME, max_recovery_attempts=policy.max_attempts)
    async def pipeline() -> dict[str, Any]:
        """The durable five-step pipeline from spec §4."""
        outcomes: list[StepOutcome] = []

        async def run(name: str, fn: Callable[..., Any], *args: Any) -> Any:
            # A fresh state machine per step: a shared one would be SUCCEEDED
            # by the time the second step started, and could not re-enter
            # RUNNING.
            state = StepStateMachine(name, registry=registry)
            state.start()
            try:
                result = await fn(*args)
                state.succeed()
                outcomes.append(state.outcome)
                annotate(**{f"aetheris.result.{name}": True})
                return result
            except Exception:
                state.fail()
                outcomes.append(state.outcome)
                try:
                    state.compensate()
                except Exception:
                    outcomes.append(state.outcome)
                    raise
                raise

        dry_run = settings.dry_run
        with span("pipeline.workflow", **{"aetheris.workflow": WORKFLOW_NAME}):
            at = (await run(STEP_TRIGGER, trigger))["at"]
            manifest = await run(STEP_INGEST, ingest, dry_run, at)
            scored = await run(STEP_SENTIMENT, sentiment, dry_run, manifest)
            graph = await run(STEP_GRAPH, graph_integration, dry_run, manifest, scored, at)
            await run(STEP_EVALUATION, evaluate, dry_run, scored, at)

        return {
            "workflow": WORKFLOW_NAME,
            "dry_run": dry_run,
            "steps": [
                {"name": o.step_name, "state": o.final_state.value, "attempts": o.attempts}
                for o in outcomes
            ],
            "summary": _summary(dry_run, manifest, scored, graph),
        }

    # Stash the registered functions so start() can find them.
    register_workflow._pipeline = pipeline  # type: ignore[attr-defined]


async def start_async(
    settings: Settings | None = None,
    *,
    wait: bool = False,
    http: HttpClient | None = None,
    watchlist: Watchlist | None = None,
) -> WorkflowHandle:
    """Start a new durable pipeline run.

    With ``wait=True`` the coroutine also awaits the workflow result, so the
    caller gets a terminal status instead of ``PENDING``. ``http`` and
    ``watchlist`` are passed to :func:`register_workflow`.
    """
    import dbos

    settings = settings or get_settings()
    register_workflow(settings, http=http, watchlist=watchlist)
    pipeline = register_workflow._pipeline  # type: ignore[attr-defined]
    handle = await dbos.DBOS.start_workflow_async(pipeline)
    workflow_id = getattr(handle, "workflow_id", "?")

    if not wait:
        return WorkflowHandle(workflow_id=workflow_id, status="PENDING")

    result: dict[str, Any] = {}
    try:
        # DBOS 3.x: the async handle exposes get_result(); the classmethods
        # get_result/get_result_async live on DBOS instead.
        result = await handle.get_result()
        status = "SUCCESS"
    except Exception as exc:
        result = {"error": str(exc)}
        status = "ERROR"
    return WorkflowHandle(
        workflow_id=workflow_id,
        status=status,
        result=result,
    )


async def run_to_completion(
    settings: Settings | None = None,
    *,
    http: HttpClient | None = None,
    watchlist: Watchlist | None = None,
) -> WorkflowHandle:
    """Start the pipeline and wait for it to finish."""
    return await start_async(settings, wait=True, http=http, watchlist=watchlist)


def start(
    settings: Settings | None = None,
    *,
    wait: bool = False,
    http: HttpClient | None = None,
    watchlist: Watchlist | None = None,
) -> WorkflowHandle:
    """Start a new durable pipeline run (synchronous convenience wrapper)."""
    return asyncio.run(start_async(settings, wait=wait, http=http, watchlist=watchlist))


# DBOS statuses of a run that is executing or waiting to execute.
_ACTIVE = frozenset({"PENDING", "ENQUEUED", "DELAYED"})


def resume_to_completion(workflow_id: str) -> WorkflowHandle:
    """Resume a pipeline run from its first failed step and wait for it to finish.

    The pipeline must already be registered (:func:`register_workflow`): DBOS
    executes a rewound run by looking its function up by name. In a fresh
    process, register before ``DBOS.launch()``, which recovers pending runs.

    Steps that already succeeded return their checkpointed outputs and are not
    executed again. A run that succeeded is not rewound at all, and one that is
    still executing, such as a run DBOS recovered at launch, is awaited. Call
    outside an event loop; DBOS's synchronous API refuses to run inside one.

    Args:
        workflow_id: The run to resume.

    Returns:
        The run's terminal status and result, as :func:`start_async` reports it.

    Raises:
        WorkflowError: When the system database has no run with this id.
    """
    import dbos

    status = checkpoints.workflow_status(workflow_id).status
    if status == "UNKNOWN":
        raise WorkflowError(f"no pipeline run {workflow_id!r} to resume")
    if status != "SUCCESS" and status not in _ACTIVE:
        checkpoints.resume(workflow_id)

    try:
        result = dbos.DBOS.get_result(workflow_id)
        outcome = "SUCCESS"
    except Exception as exc:
        result = {"error": str(exc)}
        outcome = "ERROR"
    return WorkflowHandle(workflow_id=workflow_id, status=outcome, result=result)


__all__ = [
    "PIPELINE_STEPS",
    "STEP_EVALUATION",
    "STEP_GRAPH",
    "STEP_INGEST",
    "STEP_SENTIMENT",
    "STEP_TRIGGER",
    "WORKFLOW_NAME",
    "WorkflowError",
    "build_registry",
    "register_workflow",
    "resume_to_completion",
    "run_to_completion",
    "start",
    "start_async",
]
