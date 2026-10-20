"""`aetheris graph upsert`, `score`, `evaluate`, and `run --resume` on stored data.

The raw store is seeded by running ``run_ingest`` against ``RoutedHttpClient``
serving the recorded fixtures, so no test makes a live provider call. Each test
writes its raw store, DuckDB file, and MLflow database under ``tmp_path``.

The two AAPL fixture articles were published around 2026-10-13 10:00 UTC, so
the CLI's clock is pinned to ``AT``, two hours later, for the commands that read
a time window.
"""

from __future__ import annotations

import contextlib
import json
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from src.cli import main as cli
from src.config.settings import Settings
from src.config.watchlist import DEFAULT_WATCHLIST
from src.graph.repository import GraphRepository
from src.graph.schema import GraphDatabase
from src.orchestration import checkpoints, steps, workflows
from src.orchestration.wiring import build_ingestion_registry
from src.orchestration.workflows import PIPELINE_STEPS
from tests.integration.fakes import RoutedHttpClient, fixture_routes

WL = DEFAULT_WATCHLIST
AAPL = "equity:NASDAQ:AAPL"
MSFT = "equity:NASDAQ:MSFT"
AT = datetime(2026, 10, 13, 12, tzinfo=UTC)
CREDENTIALS = (
    "FRED_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
    "FINNHUB_API_KEY",
    "FMP_API_KEY",
    "TIINGO_API_KEY",
)


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    """Return the raw store and graph paths; cwd moves to ``tmp_path`` for MLflow."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AETHERIS_CONFIG", raising=False)
    monkeypatch.setattr(cli, "_now", lambda: AT)
    return str(tmp_path / "raw.db"), str(tmp_path / "graph.duckdb")


def _seed(raw: str) -> None:
    """Fill the raw store with one ingest of the recorded fixtures."""
    settings = Settings(
        raw_db_path=raw,
        fred_api_key="fred-key",
        finnhub_api_key="finnhub-key",
        tiingo_api_key="tiingo-key",
        activity_timeout_s=1.0,
    )
    registry = build_ingestion_registry(settings, http=RoutedHttpClient(fixture_routes()))
    try:
        steps.run_ingest(registry, WL, now=AT)
    finally:
        registry.payload_store.close()


def _cli(capsys, *argv: str) -> tuple[int, str, str]:
    """Run the CLI in-process and return its exit code, stdout, and stderr."""
    capsys.readouterr()
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _upsert(capsys, raw: str, db: str) -> dict:
    """Run ``graph upsert --json`` and return the counts it printed."""
    code, out, err = _cli(capsys, "--db", db, "--raw", raw, "--json", "graph", "upsert")
    assert code == cli.EXIT_OK, err
    return json.loads(out)


def _evaluate(capsys, db: str, *options: str) -> dict:
    """Run ``evaluate --json`` with ``options`` and return the evaluation."""
    code, out, err = _cli(capsys, "--db", db, "--json", "evaluate", *options)
    assert code == cli.EXIT_OK, err
    return json.loads(out)["evaluation"]


# ---------------------------------------------------------------------------
# graph upsert
# ---------------------------------------------------------------------------


def test_graph_upsert_writes_the_graph(paths, capsys):
    """The stored news is scored, aligned, and upserted; the counts are printed."""
    raw, db = paths
    _seed(raw)

    counts = _upsert(capsys, raw, db)

    assert counts["edges"]["TARGETS"] > 0
    assert counts["assets"] == len(WL.assets)
    with closing(GraphDatabase(db)) as graph:
        written = GraphRepository(graph).counts()
    assert written["sentiment_metric"] == counts["metrics"] > 0
    assert written["graph_edge"] >= counts["edges"]["TARGETS"]


def test_graph_upsert_without_payloads_is_a_usage_error(paths, capsys):
    """With an empty raw store nothing is written and the command says so."""
    raw, db = paths

    code, _, err = _cli(capsys, "--db", db, "--raw", raw, "graph", "upsert")

    assert code == cli.EXIT_USAGE
    assert "nothing to upsert" in err


def test_graph_upsert_window_limits_the_metrics(paths, capsys):
    """``--since``/``--until`` keep only the articles published in the window."""
    raw, db = paths
    _seed(raw)

    code, out, err = _cli(
        capsys, "--db", db, "--raw", raw, "--json", "graph", "upsert",
        "--since", "2026-10-13T09:58:00+00:00", "--until", "2026-10-13T10:00:00+00:00",
    )

    assert code == cli.EXIT_OK, err
    assert json.loads(out)["metrics"] == 1


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------


def test_score_reads_stored_news(paths, capsys):
    """Without ``--text`` the stored news is scored for the watched assets."""
    raw, _ = paths
    _seed(raw)

    code, out, err = _cli(capsys, "--raw", raw, "score")

    assert code == cli.EXIT_OK, err
    assert AAPL in out


def test_score_filters_stored_news(paths, capsys):
    """``--entity`` filters by entity id and ``--since`` by publication time."""
    raw, _ = paths
    _seed(raw)

    _, out, _ = _cli(capsys, "--raw", raw, "--json", "score", "--entity", AAPL)
    aapl = json.loads(out)
    _, out, _ = _cli(capsys, "--raw", raw, "--json", "score", "--entity", MSFT)
    msft = json.loads(out)
    _, out, _ = _cli(
        capsys, "--raw", raw, "--json", "score", "--since", "2026-10-13T09:58:00+00:00"
    )
    recent = json.loads(out)

    assert len(aapl) == 2
    assert {p["entity_id"] for p in aapl} == {AAPL}
    assert msft == []
    assert [p["source_reference"] for p in recent] == ["finnhub:news:8614108"]


def test_score_without_stored_news_is_a_usage_error(paths, capsys):
    """With nothing stored and no ``--text`` there is nothing to score."""
    raw, _ = paths

    code, _, err = _cli(capsys, "--raw", raw, "score")

    assert code == cli.EXIT_USAGE
    assert "aetheris ingest" in err


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


def test_evaluate_reads_the_graph(paths, capsys):
    """The last 24 hours of SentimentMetric rows are the evaluated items."""
    raw, db = paths
    _seed(raw)
    written = _upsert(capsys, raw, db)["metrics"]

    evaluation = _evaluate(capsys, db)

    assert written > 0
    assert evaluation["items_scored"] == written


def test_evaluate_options_select_the_rows(paths, capsys, monkeypatch):
    """``--entity``, ``--min-confidence``, ``--since``, and the window pick the rows."""
    raw, db = paths
    _seed(raw)
    written = _upsert(capsys, raw, db)["metrics"]

    aapl = _evaluate(capsys, db, "--entity", AAPL)
    msft = _evaluate(capsys, db, "--entity", MSFT)
    confident = _evaluate(capsys, db, "--min-confidence", "0.75")
    monkeypatch.setattr(cli, "_now", lambda: AT + timedelta(days=1))
    next_day = _evaluate(capsys, db)
    since = _evaluate(capsys, db, "--since", "2026-10-13T00:00:00+00:00")

    assert (aapl["items_scored"], aapl["entity_id"]) == (written, AAPL)
    assert msft["items_scored"] == 0
    assert confident["items_scored"] == 1
    assert next_day["items_scored"] == 0
    assert since["items_scored"] == written


def test_evaluate_on_empty_graph(paths, capsys):
    """An empty graph evaluates nothing and still succeeds."""
    _, db = paths

    code, out, err = _cli(capsys, "--db", db, "evaluate")

    assert code == cli.EXIT_OK, err
    assert "evaluated 0 item(s)" in out


# ---------------------------------------------------------------------------
# run --resume
# ---------------------------------------------------------------------------


def _run_settings(raw: str, db: str) -> Settings:
    return Settings(
        db_path=db,
        raw_db_path=raw,
        fred_api_key="fred-key",
        finnhub_api_key="finnhub-key",
        tiingo_api_key="tiingo-key",
        retry_max_attempts=1,  # a failing step fails the run at once
        activity_timeout_s=60.0,
    )


def _count_calls(monkeypatch, *, fail_graph_once: bool) -> dict[str, int]:
    """Count ingest and graph step calls; a second ingest fails instead of fetching."""
    calls = {"ingest": 0, "graph": 0}
    real_ingest, real_graph = steps.run_ingest, steps.run_graph_integration

    def ingest_once(*args, **kwargs):
        calls["ingest"] += 1
        if calls["ingest"] > 1:
            raise AssertionError("ingest ran again")
        return real_ingest(*args, **kwargs)

    def graph(*args, **kwargs):
        calls["graph"] += 1
        if fail_graph_once and calls["graph"] == 1:
            raise RuntimeError("simulated graph failure")
        return real_graph(*args, **kwargs)

    monkeypatch.setattr(steps, "run_ingest", ingest_once)
    monkeypatch.setattr(steps, "run_graph_integration", graph)
    return calls


def _start_run(dbos, raw: str, db: str) -> tuple[str, str, object]:
    """Run the pipeline over the fixtures; return its id, status, and result.

    The run is started through DBOS's synchronous API, not ``asyncio.run``:
    DBOS makes its own executor the event loop's default, and ``asyncio.run``
    shuts that executor down on exit, so no later step in this process could
    run. A CLI resume is a fresh process and never meets this.
    """
    workflows.register_workflow(
        _run_settings(raw, db), http=RoutedHttpClient(fixture_routes()), watchlist=WL
    )
    handle = dbos.DBOS.start_workflow(workflows.register_workflow._pipeline)
    result = None
    with contextlib.suppress(Exception):  # a failed run is a test subject
        result = handle.get_result()
    return handle.workflow_id, handle.get_status().status, result


def _resume(capsys, monkeypatch, raw: str, db: str, workflow_id: str) -> tuple[int, str, str]:
    """Run ``run --resume`` with placeholder credentials for the pre-check."""
    for name in CREDENTIALS:
        monkeypatch.setenv(name, "test-key")
    return _cli(capsys, "--db", db, "--raw", raw, "--json", "run", "--resume", workflow_id)


def test_resume_runs_the_failed_steps(dbos_env, paths, capsys, monkeypatch):
    """A run failed at graph integration completes when resumed, without fetching again."""
    raw, db = paths
    calls = _count_calls(monkeypatch, fail_graph_once=True)
    workflow_id, status, _ = _start_run(dbos_env, raw, db)
    assert status == "ERROR"
    # In this process the pipeline is already registered, so record that the CLI
    # registers it itself, before launch, as a fresh process needs.
    order: list[str] = []
    register, launch = workflows.register_workflow, checkpoints.launch

    def spy_register(*args, **kwargs):
        order.append("register")
        return register(*args, **kwargs)

    def spy_launch(*args, **kwargs):
        order.append("launch")
        return launch(*args, **kwargs)

    monkeypatch.setattr(workflows, "register_workflow", spy_register)
    monkeypatch.setattr(checkpoints, "launch", spy_launch)

    code, out, err = _resume(capsys, monkeypatch, raw, db, workflow_id)

    assert code == cli.EXIT_OK, err
    assert order == ["register", "launch"]
    report = json.loads(out)
    assert (report["action"], report["status"]) == ("resumed", "SUCCESS")
    assert [(s["name"], s["state"]) for s in report["result"]["steps"]] == [
        (name, "SUCCEEDED") for name in PIPELINE_STEPS
    ]
    assert calls == {"ingest": 1, "graph": 2}
    with closing(GraphDatabase(db)) as graph:
        metrics = GraphRepository(graph).counts()["sentiment_metric"]
    assert metrics == report["result"]["summary"]["scored"] > 0


def test_resume_of_a_finished_run_does_not_run_it_again(dbos_env, paths, capsys, monkeypatch):
    """Resuming a run that succeeded reports its result and re-runs no step."""
    raw, db = paths
    calls = _count_calls(monkeypatch, fail_graph_once=False)
    workflow_id, status, result = _start_run(dbos_env, raw, db)
    assert status == "SUCCESS"

    code, out, err = _resume(capsys, monkeypatch, raw, db, workflow_id)

    assert code == cli.EXIT_OK, err
    assert json.loads(out)["result"] == result
    assert calls == {"ingest": 1, "graph": 1}


def test_resume_of_an_unknown_run_fails(dbos_env, paths, capsys, monkeypatch):
    """An id the engine has never seen is a runtime error naming the id."""
    raw, db = paths

    code, _, err = _resume(capsys, monkeypatch, raw, db, "no-such-run")

    assert code == cli.EXIT_RUNTIME
    assert "no-such-run" in err
