"""Shared fixtures for the durable-workflow integration tests."""

from __future__ import annotations

import contextlib

import pytest


@pytest.fixture()
def dbos_env(tmp_path, monkeypatch):
    """Configure DBOS against a throwaway SQLite system database.

    The working directory moves to ``tmp_path`` as well, because the evaluation
    step logs to MLflow's default ``sqlite:///mlflow.db`` in the current
    directory.
    """
    import dbos

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    db_file = tmp_path / "system.dbos.sqlite"
    config = {
        "name": "aetheris_test",
        "system_database_url": f"sqlite:///{db_file}",
        "log_level": "WARN",
    }
    dbos.DBOS(config=config)
    dbos.DBOS.launch()
    yield dbos
    with contextlib.suppress(Exception):  # teardown is best-effort
        dbos.DBOS.destroy(destroy_registry=True)
