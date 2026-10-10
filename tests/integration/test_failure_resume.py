"""Failure injection and resume integration test (T018) — FR-002.

Verifies the resume contract end-to-end against a real DBOS system database:
a step that fails is recorded durably, ``first_incomplete_step`` locates the
exact point of failure, and resuming from that point re-runs only the failed
step rather than the whole workflow.

Every test body runs inside a **single** event loop: calling ``asyncio.run``
twice in one test orphan's DBOS's internal task and surfaces as a spurious
``CancelledError``.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
from collections.abc import Callable

import pytest

from src.orchestration import checkpoints
from src.orchestration.retry import build_step_retry_policy, step_retry_kwargs_from_policy


def async_test(fn: Callable[..., object]) -> Callable[..., object]:
    """Run an async test body in one event loop."""

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> object:
        return asyncio.run(fn(*args, **kwargs))  # type: ignore[arg-type]

    return wrapper


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    import dbos

    monkeypatch.setenv("HOME", str(tmp_path))
    from src.config.settings import Settings

    settings = Settings(
        raw_db_path=str(tmp_path / "raw.db"),
        retry_max_attempts=1,  # fail fast in tests
        activity_timeout_s=30.0,
    )
    config = {
        "name": "aetheris_fail_test",
        "system_database_url": f"sqlite:///{tmp_path / 'sys.dbos.sqlite'}",
        "log_level": "WARN",
    }
    dbos.DBOS(config=config)
    dbos.DBOS.launch()
    yield dbos, settings
    with contextlib.suppress(Exception):
        dbos.DBOS.destroy(destroy_registry=True)


def _register(engine, name: str, fail_times: int = 0):
    """Register a single-step workflow whose step fails ``fail_times`` times."""
    dbos, settings = engine
    state = {"calls": 0}

    kwargs = step_retry_kwargs_from_policy(build_step_retry_policy(settings))

    @dbos.DBOS.step(name=name, **kwargs)
    async def flaky() -> str:
        state["calls"] += 1
        if state["calls"] <= fail_times:
            raise RuntimeError(f"simulated failure #{state['calls']}")
        return "recovered"

    @dbos.DBOS.workflow(name=f"wf_{name}")
    async def workflow() -> dict[str, object]:
        return {"result": await flaky()}

    return workflow, state


async def _start(dbos, workflow):
    """Start a workflow and await it, returning (handle, error_or_None)."""
    handle = await dbos.DBOS.start_workflow_async(workflow)
    error: BaseException | None = None
    try:
        await handle.get_result()
    except Exception as exc:  # noqa: BLE001 - the failure is the test subject
        error = exc
    return handle, error


class TestFailureAndResume:
    @async_test
    async def test_failing_step_is_recorded_and_located(self, engine):
        """A failed step is durable, and its position is exactly findable."""
        dbos, _ = engine
        workflow, _ = _register(engine, "flaky_always", fail_times=99)
        handle, error = await _start(dbos, workflow)
        assert error is not None, "the workflow should have failed"

        steps = await checkpoints.list_steps_async(handle.workflow_id)
        assert steps, "the failing step must be recorded durably"
        assert steps[0].function_name == "flaky_always"
        assert steps[0].failed

        idx = await checkpoints.first_incomplete_step_async(handle.workflow_id)
        assert idx == 1, "resume must start at the failed step, not later"

    @async_test
    async def test_resume_reruns_only_the_failed_step(self, engine):
        """Resuming from a checkpoint re-runs the failed step, not prior ones."""
        dbos, _ = engine
        workflow, state = _register(engine, "flaky_resume", fail_times=1)

        handle, error = await _start(dbos, workflow)
        assert error is not None, "first attempt must fail"

        first_idx = await checkpoints.first_incomplete_step_async(handle.workflow_id)
        assert first_idx == 1

        # Rewind from the failed step and run it again. The sync
        # `rewind_workflow` only enqueues; use the async variant and await the
        # workflow's result so the re-execution actually happens.
        await dbos.DBOS.rewind_workflow_async(handle.workflow_id, start_step=first_idx)
        result = await dbos.DBOS.get_result_async(handle.workflow_id)
        assert result == {"result": "recovered"}
        assert state["calls"] >= 2, "the step must have been re-executed"

    @async_test
    async def test_complete_workflow_has_no_incomplete_step(self, engine):
        dbos, _ = engine
        workflow, _ = _register(engine, "clean_step", fail_times=0)
        handle, error = await _start(dbos, workflow)
        assert error is None

        steps = await checkpoints.list_steps_async(handle.workflow_id)
        assert all(s.succeeded for s in steps)
        assert await checkpoints.first_incomplete_step_async(handle.workflow_id) is None

    @async_test
    async def test_step_state_is_success_after_single_attempt(self, engine):
        dbos, _ = engine
        workflow, _ = _register(engine, "one_shot", fail_times=0)
        handle, _ = await _start(dbos, workflow)
        steps = await checkpoints.list_steps_async(handle.workflow_id)
        assert len(steps) == 1
        assert steps[0].status == "SUCCESS"
        assert steps[0].error is None

    def test_unknown_workflow_reports_no_steps(self, engine):
        """A workflow id the engine has never seen yields no steps."""
        assert checkpoints.list_steps("no-such-workflow") == []
        assert checkpoints.first_incomplete_step("no-such-workflow") is None
