"""Step-level checkpointing and resume (FR-002).

Spec §3.1 requires "automatic resumption upon failure". Clarification Q5/A fixed
the granularity: **one checkpoint per workflow step**, and resumption restarts
from the first step lacking a successful checkpoint, so no work is re-executed
once a step completes.

DBOS provides this natively via its durable execution journal, so this module is
a thin, testable wrapper rather than a second state machine. It exposes the
two operations the CLI needs (``aetheris run --resume`` and status inspection)
and translates DBOS workflow handles into Aetheris' own vocabulary.

Key DBOS 3.x facts baked in here (verified against the installed library):
  * ``system_database_url`` defaults to ``sqlite:///{name}`` — no server needed.
  * ``@DBOS.workflow(max_recovery_attempts=...)`` controls resume attempts.
  * ``@DBOS.step(timeout_seconds=...)`` provides the activity timeout (FR-003).
  * ``DBOS.rewind_workflow(id, start_step=N)`` resumes from a specific step.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.config.settings import Settings
from src.orchestration.retry import build_step_retry_policy


class CheckpointError(RuntimeError):
    """Raised when the workflow engine cannot be configured or queried."""


@dataclass(frozen=True)
class StepRecord:
    """One durable step as reported by the engine."""

    function_name: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """Handle the succeeded."""
        return self.status.upper() == "SUCCESS"

    @property
    def failed(self) -> bool:
        """Handle the failed."""
        return self.status.upper() == "ERROR"


@dataclass(frozen=True)
class WorkflowHandle:
    """A started workflow, in Aetheris terms."""

    workflow_id: str
    status: str
    started_at: datetime | None = None
    name: str | None = None
    result: Any = None


def dbos_config(settings: Settings, *, app_name: str = "aetheris") -> dict[str, Any]:
    """Build the DBOS configuration dict.

    The system database is SQLite by default so the pipeline runs with no
    external service (constitution: self-hosted, zero lock-in).
    """
    policy = build_step_retry_policy(settings)
    return {
        "name": app_name,
        "system_database_url": f"sqlite:///{settings.raw_db_path}.dbos.sqlite",
        "enable_otlp": settings.otlp_endpoint is not None,
        "otlp_traces_endpoints": [settings.otlp_endpoint] if settings.otlp_endpoint else [],
        "application_version": "0.1.0",
        # A step's activity timeout also bounds recovery time (SC-002).
        "max_recovery_attempts": policy.max_attempts,
    }


def configure(settings: Settings, *, app_name: str = "aetheris") -> None:
    """Construct the global DBOS instance from Aetheris configuration.

    Must precede :func:`launch`; DBOS raises if ``launch()`` is called without
    an instance. Safe to call more than once.
    """
    try:
        import dbos
    except ImportError as exc:  # pragma: no cover - dependency always installed
        raise CheckpointError(
            "dbos is not installed; it is the durable-execution engine (research.md §A)"
        ) from exc
    try:
        dbos.DBOS(config=dbos_config(settings, app_name=app_name))
    except Exception as exc:
        raise CheckpointError(f"failed to configure durable engine: {exc}") from exc


def launch(settings: Settings | None = None, *, app_name: str = "aetheris") -> None:
    """Configure (if needed) and launch the DBOS runtime. Idempotent."""
    if settings is not None:
        configure(settings, app_name=app_name)
    try:
        import dbos

        dbos.DBOS.launch()
    except Exception as exc:  # already launched / config error
        raise CheckpointError(f"failed to launch durable engine: {exc}") from exc


__all__ = [
    "CheckpointError",
    "StepRecord",
    "WorkflowHandle",
    "configure",
    "dbos_config",
    "first_incomplete_step",
    "launch",
    "list_steps",
    "recent_workflows",
    "resume",
    "workflow_status",
]


def _epoch_ms_to_dt(value: Any) -> datetime | None:
    """Convert Unix epoch milliseconds to a tz-aware UTC datetime."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return _epoch_ms_to_dt(value)


def _field(info: object, key: str, default: object = None) -> object:
    """Read a field from a DBOS StepInfo, which is a dict, not an object."""
    if isinstance(info, dict):
        return info.get(key, default)
    return getattr(info, key, default)


async def list_steps_async(workflow_id: str) -> list[StepRecord]:
    """Async variant of :func:`list_steps` for use inside a running event loop."""
    import dbos

    try:
        raw = await dbos.DBOS.list_workflow_steps_async(workflow_id)
    except Exception as exc:
        raise CheckpointError(f"could not read steps for {workflow_id}: {exc}") from exc
    return _to_step_records(raw)


def list_steps(workflow_id: str) -> list[StepRecord]:
    """Return the durable steps recorded for a workflow, in execution order.

    DBOS's ``StepInfo`` is a TypedDict with no ``status`` field: a step is
    considered failed exactly when it carries an ``error``. Timestamps are Unix
    epoch milliseconds.

    Only safe outside a running event loop; use :func:`list_steps_async` inside
    one (the CLI runs outside, tests run inside).
    """
    import dbos

    try:
        raw = dbos.DBOS.list_workflow_steps(workflow_id)
    except Exception as exc:
        raise CheckpointError(f"could not read steps for {workflow_id}: {exc}") from exc
    return _to_step_records(raw)


def _to_step_records(raw: object) -> list[StepRecord]:
    records: list[StepRecord] = []
    for info in raw or []:
        error = _field(info, "error")
        records.append(
            StepRecord(
                function_name=str(_field(info, "function_name", "?")),
                status="ERROR" if error else "SUCCESS",
                started_at=_epoch_ms_to_dt(_field(info, "started_at_epoch_ms")),
                completed_at=_epoch_ms_to_dt(_field(info, "completed_at_epoch_ms")),
                error=str(error) if error else None,
            )
        )
    return records


async def first_incomplete_step_async(workflow_id: str) -> int | None:
    """Async variant of :func:`first_incomplete_step`."""
    return _first_incomplete(await list_steps_async(workflow_id))


def first_incomplete_step(workflow_id: str) -> int | None:
    """1-based index of the first step that has not succeeded, or None.

    This is the "exact point of failure" from constitution §III: resumption
    starts here, so completed steps are never re-executed.
    """
    return _first_incomplete(list_steps(workflow_id))


def _first_incomplete(records: list[StepRecord]) -> int | None:
    if not records:
        return None
    for idx, rec in enumerate(records, start=1):
        if rec.failed:
            return idx
    if records[-1].succeeded:
        return None
    return len(records) + 1



def resume(workflow_id: str, *, start_step: int | None = None) -> WorkflowHandle:
    """Resume a workflow from its first incomplete step (or an explicit one).

    Mirrors ``aetheris run --resume WORKFLOW_ID`` in contracts/cli.md.
    """
    import dbos

    step = start_step if start_step is not None else first_incomplete_step(workflow_id)
    try:
        handle = dbos.DBOS.rewind_workflow(workflow_id, start_step=step)
    except Exception as exc:
        raise CheckpointError(f"could not rewind {workflow_id}: {exc}") from exc
    return WorkflowHandle(
        workflow_id=workflow_id,
        status=str(getattr(handle, "status", "ENQUEUED")),
    )


def workflow_status(workflow_id: str) -> WorkflowHandle:
    """Handle the workflow status."""
    import dbos

    try:
        statuses = dbos.DBOS.list_workflows(workflow_ids=[workflow_id])
    except Exception as exc:
        raise CheckpointError(f"could not read status for {workflow_id}: {exc}") from exc
    if not statuses:
        return WorkflowHandle(workflow_id=workflow_id, status="UNKNOWN")
    s = statuses[0]
    return WorkflowHandle(
        workflow_id=workflow_id,
        status=str(getattr(s, "status", "?")),
        started_at=_parse_ts(getattr(s, "created_at", None)),
        name=getattr(s, "name", None),
    )


def recent_workflows(limit: int = 20) -> Iterable[WorkflowHandle]:
    """Handle the recent workflows."""
    import dbos

    try:
        statuses = dbos.DBOS.list_workflows(limit=limit, sort_desc=True)
    except Exception as exc:
        raise CheckpointError(f"could not list workflows: {exc}") from exc
    for s in statuses or []:
        wid = getattr(s, "workflow_id", None)
        if wid is None:
            continue
        yield WorkflowHandle(
            workflow_id=wid,
            status=str(getattr(s, "status", "?")),
            started_at=_parse_ts(getattr(s, "created_at", None)),
            name=getattr(s, "name", None),
        )


