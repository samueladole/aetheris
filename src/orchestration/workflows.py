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
required by spec §3.1 / FR-003.

Only the *orchestration shell* is complete here. The step bodies delegate to the
ingestion/sentiment/graph layers, which are filled in by US2–US5; until then the
ingestion and sentiment steps are explicit no-ops guarded by ``dry_run`` so the
workflow is runnable and testable on its own (US1's independent test).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.config.settings import Settings, get_settings
from src.observability.metrics import get_metrics
from src.observability.telemetry import annotate, span
from src.orchestration.checkpoints import WorkflowHandle
from src.orchestration.compensation import (
    CompensationRegistry,
    StepOutcome,
    StepStateMachine,
)
from src.orchestration.retry import build_step_retry_policy, step_retry_kwargs_from_policy

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


def register_workflow(settings: Settings) -> None:
    """Register the pipeline workflow with the DBOS runtime.

    Imported lazily so this module can be imported without a DBOS runtime being
    configured (Principle II: layers stay importable in isolation).
    """
    import dbos

    policy = build_step_retry_policy(settings)
    step_kwargs = step_retry_kwargs_from_policy(policy)

    @dbos.DBOS.step(name=STEP_TRIGGER, **step_kwargs)
    async def trigger() -> dict[str, Any]:
        """Step 1: resolve the trigger and report the run parameters."""
        with span("pipeline.trigger", **{"aetheris.step": STEP_TRIGGER}):
            return {
                "trigger": "schedule",
                "dry_run": settings.dry_run,
                "retry": {
                    "base_delay_ms": policy.base_delay_ms,
                    "multiplier": policy.multiplier,
                    "max_attempts": policy.max_attempts,
                    "jitter": policy.jitter,
                },
            }

    @dbos.DBOS.step(name=STEP_INGEST, **step_kwargs)
    async def ingest(dry_run: bool) -> dict[str, Any]:
        """Step 2: fetch raw payloads from all five providers."""
        get_metrics().fetch_total.add(1)
        with span("pipeline.ingest", **{"aetheris.step": STEP_INGEST}):
            if dry_run:
                return {"fetched": 0, "dry_run": True}
            # US2 wires src.ingestion.registry here.
            return {"fetched": 0, "providers": []}

    @dbos.DBOS.step(name=STEP_SENTIMENT, **step_kwargs)
    async def sentiment(dry_run: bool) -> dict[str, Any]:
        """Step 3: score unstructured text from the ingested payloads."""
        with span("pipeline.sentiment", **{"aetheris.step": STEP_SENTIMENT}):
            if dry_run:
                return {"scored": 0, "dry_run": True}
            # US3 wires src.sentiment.analyzer here.
            return {"scored": 0}

    @dbos.DBOS.step(name=STEP_GRAPH, **step_kwargs)
    async def graph_integration(dry_run: bool) -> dict[str, Any]:
        """Step 4: backward as-of align, then upsert into the knowledge graph."""
        get_metrics().graph_upserts.add(1)
        with span("pipeline.graph", **{"aetheris.step": STEP_GRAPH}):
            if dry_run:
                return {"upserted": 0, "dry_run": True}
            # US4 wires src.graph.align + src.graph.repository here.
            return {"upserted": 0, "edges": 0}

    @dbos.DBOS.step(name=STEP_EVALUATION, **step_kwargs)
    async def evaluate(dry_run: bool) -> dict[str, Any]:
        """Step 5: validate telemetry and output consistency (FR-019)."""
        with span("pipeline.evaluate", **{"aetheris.step": STEP_EVALUATION}):
            if dry_run:
                return {"evaluated": 0, "dry_run": True}
            # US5 wires src.observability.evaluate here.
            return {"evaluated": 0}

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

        with span("pipeline.workflow", **{"aetheris.workflow": WORKFLOW_NAME}):
            await run(STEP_TRIGGER, trigger)
            await run(STEP_INGEST, ingest, settings.dry_run)
            await run(STEP_SENTIMENT, sentiment, settings.dry_run)
            await run(STEP_GRAPH, graph_integration, settings.dry_run)
            await run(STEP_EVALUATION, evaluate, settings.dry_run)

        return {
            "workflow": WORKFLOW_NAME,
            "dry_run": settings.dry_run,
            "steps": [
                {"name": o.step_name, "state": o.final_state.value, "attempts": o.attempts}
                for o in outcomes
            ],
        }

    # Stash the registered functions so start() can find them.
    register_workflow._pipeline = pipeline  # type: ignore[attr-defined]


async def start_async(
    settings: Settings | None = None, *, wait: bool = False
) -> WorkflowHandle:
    """Start a new durable pipeline run.

    With ``wait=True`` the coroutine also awaits the workflow result, so the
    caller gets a terminal status instead of ``PENDING``.
    """
    import dbos

    settings = settings or get_settings()
    register_workflow(settings)
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


async def run_to_completion(settings: Settings | None = None) -> WorkflowHandle:
    """Start the pipeline and wait for it to finish."""
    return await start_async(settings, wait=True)


def start(
    settings: Settings | None = None, *, wait: bool = False
) -> WorkflowHandle:
    """Start a new durable pipeline run (synchronous convenience wrapper)."""
    import asyncio

    return asyncio.run(start_async(settings, wait=wait))


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
    "start",
    "start_async",
]
