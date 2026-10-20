"""The durable workflow runs the real pipeline steps.

DBOS runs against a throwaway SQLite system database (the ``dbos_env``
fixture), every provider response comes from ``RoutedHttpClient`` serving the
recorded fixtures, and each test writes its own raw store and DuckDB file under
``tmp_path``, so no test makes a live provider call or touches the repository.
"""

from __future__ import annotations

import asyncio
import dataclasses
from contextlib import closing
from datetime import datetime

from src.config.settings import Settings
from src.config.watchlist import DEFAULT_WATCHLIST
from src.graph.repository import GraphRepository
from src.graph.schema import GraphDatabase
from src.orchestration import checkpoints, steps
from src.orchestration.workflows import PIPELINE_STEPS, STEP_GRAPH, run_to_completion
from tests.integration.fakes import RoutedHttpClient, fixture_routes

WL = DEFAULT_WATCHLIST


def _settings(tmp_path, **overrides) -> Settings:
    base = Settings(
        db_path=str(tmp_path / "graph.duckdb"),
        raw_db_path=str(tmp_path / "raw.db"),
        fred_api_key="fred-key",
        finnhub_api_key="finnhub-key",
        tiingo_api_key="tiingo-key",
        retry_max_attempts=1,  # a failing step fails the run at once
        activity_timeout_s=60.0,
    )
    return dataclasses.replace(base, **overrides)


def _counts(settings: Settings) -> dict[str, int]:
    with closing(GraphDatabase(settings.db_path)) as db:
        return GraphRepository(db).counts()


def test_run_writes_the_graph(dbos_env, tmp_path):
    """A live run fetches the watchlist, scores the news, and writes the graph."""
    settings = _settings(tmp_path)
    routed = RoutedHttpClient(fixture_routes())

    handle = asyncio.run(run_to_completion(settings, http=routed, watchlist=WL))

    assert handle.status == "SUCCESS", handle.result
    assert [s["name"] for s in handle.result["steps"]] == list(PIPELINE_STEPS)
    assert all(s["state"] == "SUCCEEDED" for s in handle.result["steps"])
    summary = handle.result["summary"]
    assert summary["fetched"] == len(WL.macro) + 2 * len(WL.assets) + 1
    assert summary["scored"] > 0
    assert summary["edges"]["TARGETS"] == summary["scored"]
    counts = _counts(settings)
    assert counts["graph_edge"] > 0
    assert counts["sentiment_metric"] == summary["scored"]


def test_resume_does_not_refetch(dbos_env, tmp_path, monkeypatch):
    """Resuming after a graph failure reuses the checkpointed ingest and its ``at``."""
    settings = _settings(tmp_path)
    routed = RoutedHttpClient(fixture_routes())
    calls = {"ingest": 0, "graph": 0}
    real_ingest, real_graph = steps.run_ingest, steps.run_graph_integration

    def counting_ingest(*args, **kwargs):
        calls["ingest"] += 1
        return real_ingest(*args, **kwargs)

    def failing_once(*args, **kwargs):
        calls["graph"] += 1
        if calls["graph"] == 1:
            raise RuntimeError("simulated graph failure")
        return real_graph(*args, **kwargs)

    monkeypatch.setattr(steps, "run_ingest", counting_ingest)
    monkeypatch.setattr(steps, "run_graph_integration", failing_once)

    # One event loop for the whole run: a second asyncio.run would orphan
    # DBOS's internal task (see test_failure_resume.py).
    async def run_then_resume():
        first = await run_to_completion(settings, http=routed, watchlist=WL)
        fetched = list(routed.calls)
        step = await checkpoints.first_incomplete_step_async(first.workflow_id)
        await dbos_env.DBOS.rewind_workflow_async(first.workflow_id, start_step=step)
        result = await dbos_env.DBOS.get_result_async(first.workflow_id)
        recorded = await dbos_env.DBOS.list_workflow_steps_async(first.workflow_id)
        return first, fetched, step, result, recorded

    first, fetched, step, result, recorded = asyncio.run(run_then_resume())

    assert first.status == "ERROR"
    assert step == PIPELINE_STEPS.index(STEP_GRAPH) + 1
    assert fetched, "the first run fetched nothing"
    assert routed.calls == fetched, "the resume fetched again"
    assert calls == {"ingest": 1, "graph": 2}
    assert all(s["state"] == "SUCCEEDED" for s in result["steps"])
    assert result["summary"]["scored"] > 0

    # The resumed graph step wrote the trigger step's `at`, not its own clock.
    at = datetime.fromisoformat(recorded[0]["output"]["at"])
    with closing(GraphDatabase(settings.db_path)) as db:
        expected = db.execute("SELECT CAST(? AS TIMESTAMP)", [at]).fetchone()[0]
        stamps = db.execute("SELECT DISTINCT computed_at FROM graph_edge").fetchall()
    assert stamps == [(expected,)]


def test_dry_run_still_makes_no_calls(dbos_env, tmp_path):
    """A dry run completes every step without contacting a provider."""
    settings = _settings(tmp_path, dry_run=True)
    routed = RoutedHttpClient(fixture_routes())

    handle = asyncio.run(run_to_completion(settings, http=routed, watchlist=WL))

    assert handle.status == "SUCCESS", handle.result
    assert all(s["state"] == "SUCCEEDED" for s in handle.result["steps"])
    assert routed.calls == []
    assert handle.result["summary"] == {"fetched": 0, "scored": 0, "edges": {}}
